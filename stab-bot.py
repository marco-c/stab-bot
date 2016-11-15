#! /usr/bin/env python
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import sys
import ssl
import threading
import json
from datetime import timedelta
import irc.bot
import irc.strings
import versions
import utils


def get_versions(channel, product='Firefox'):
    channel = channel.lower()
    version = str(versions.get(base=True)[channel])

    r = utils.get_with_retries('https://crash-stats.mozilla.com/api/ProductVersions', params={
        'product': product,
        'active': True,
        'is_rapid_beta': False,
    })

    if r.status_code != 200:
        print(r.text)
        raise Exception(r)

    return [result['version'] for result in r.json()['hits'] if result['version'].startswith(version) and result['build_type'] == channel]


def get_top(number, channel, days=3, product='Firefox'):
    versions = get_versions(channel, product)

    url = 'https://crash-stats.mozilla.com/api/SuperSearch'

    params = {
        'product': product,
        'date': ['>=' + str(utils.utc_today() - timedelta(days) + timedelta(1))],
        'version': versions,
        '_results_number': 0,
        '_facets_size': number,
    }

    r = utils.get_with_retries(url, params=params)

    if r.status_code != 200:
        print(r.text)
        raise Exception(r)

    return [signature['term'] for signature in r.json()['facets']['signature']]


def get_suspicious_signatures():
    suspicious_signatures = []

    for channel in ['release', 'beta', 'aurora', 'nightly']:
        signatures = get_top(300, channel)

        for rank, signature in enumerate(signatures):
            if any(word in signature for word in ['npswf32', 'FlashPlayer', 'flashplayerplugin', 'xul.dll@', 'XUL@', 'libxul.so@']):
                suspicious_signatures.append((rank, channel, signature))

    return suspicious_signatures


class StabBot(irc.bot.SingleServerIRCBot):
    def __init__(self, channels):
        ssl_factory = irc.connection.Factory(wrapper=ssl.wrap_socket)
        irc.bot.SingleServerIRCBot.__init__(self, [('irc.mozilla.org', 6697)], 'stab-bot', 'stab-bot', connect_factory=ssl_factory)
        self.irc_channels = channels
        self.scheduled_task = None

    def on_nicknameinuse(self, c, e):
        c.nick(c.get_nickname() + '_')

    def on_welcome(self, c, e):
        for channel, pwd in self.irc_channels:
            c.join(channel + ' ' + pwd)

        self.send_suspicious()

    def on_privmsg(self, c, e):
        self.do_command(e, e.arguments[0])

    def on_pubmsg(self, c, e):
        a = e.arguments[0].split(':', 1)
        if len(a) > 1 and irc.strings.lower(a[0]) == irc.strings.lower(self.connection.get_nickname()):
            self.do_command(e, a[1].strip())

    def on_disconnect(self, c, e):
        if self.scheduled_task is not None:
            self.scheduled_task.cancel()

    def do_command(self, e, cmd):
        if cmd == 'die':
            self.die()
        elif cmd == 'stats':
            self.connection.privmsg(e.target, e.source.nick + ': OK')
        else:
            self.connection.privmsg(e.target, e.source.nick + ': Unknown command <' + cmd + '>')

    def send_suspicious(self):
        suspicious_signatures = get_suspicious_signatures()

        if len(suspicious_signatures) > 0:
            for c, pwd in self.irc_channels:
                self.connection.privmsg(c, 'marco: Found some signatures with missing symbols:')

                for rank, channel, signature in suspicious_signatures:
                    self.connection.privmsg(c, '    #' + str(rank) + ' on ' + channel + ': ' + signature)

        self.scheduled_task = threading.Timer(21600, self.send_suspicious)  # Once every 6 hours.
        self.scheduled_task.start()


if __name__ == '__main__':
    with open(sys.argv[1]) as f:
        channels = json.load(f)

    StabBot(channels).start()
