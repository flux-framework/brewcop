#!/usr/bin/env python3

import os
import time
from datetime import datetime
from enum import Enum
from pathlib import PosixPath
import argparse
import subprocess
import random
from collections import deque, namedtuple, defaultdict

from slackclient import SlackClient

class WeightNotStableError(RuntimeError):
    pass

def get_current_weight():
    query_cmd = [str(args.query_exe.resolve())]
    if args.query_exe.name == "fake-query.py":
        query_cmd.extend(['-p', str(args.pot), '-w', str(args.water), '-t', str(args.tolerance)])
    subproc = subprocess.Popen(query_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = subproc.communicate()
    if stderr and len(stderr) > 0:
        try:
            stderr = stderr.decode('utf-8')
        except UnicodeDecodeError:
            raise RuntimeError("Failed to decode stderr: {}".format(stderr.hex()))
        if "Over capacity" in stderr:
            return float("inf")
        elif "Under capacity" in stderr:
            return 0.0
        elif "Weight not stable" in stderr:
            raise WeightNotStableError()
        else:
            raise RuntimeError()
    return float(stdout)

def get_notification_template():
    return random.choice([
        "{:.1f} ounces of hot, fresh coffee are ready for consumption in B451.",
        "Please drink the {:.1f} ounces of coffee in B451.  You have 20 seconds to comply.",
        "You must consume the {:.1f} ounces of coffee or be terminated.",
        "{:.1f} ounces of human productivity beverage available for consumption in B451",
        ])

def send_slack_notification(weight_ounces, channel="#flux"):
    now = datetime.now()
    if ((now - send_slack_notification.last_notify[channel]).total_seconds() > 60 * 60):
        message_template = get_notification_template()
        message = message_template.format(weight_ounces)
        print("Sending slack notification: {}".format(message))
        sc.api_call("chat.postMessage", channel=channel, text=message)
        send_slack_notification.last_notify[channel] = now
send_slack_notification.last_notify = defaultdict(lambda: datetime.fromtimestamp(0))

class ScaleStates(Enum):
    NOPOT = "no pot"
    EMPTYPOT = "empty pot"
    COFFEEPOT = "pot with coffee"
    OVERWEIGHT = "overweight"
    ERROR = "error"

def pounds_to_fl_ounces(pounds):
    return pounds * 15.34

class Reading():
    def __init__(self, weight):
        self.weight = weight

    @property
    def state(self):
        if self.weight < args.tolerance:
            return ScaleStates.NOPOT
        if (args.pot + args.tolerance) > self.weight > (args.pot - args.tolerance):
            return ScaleStates.EMPTYPOT
        if (args.pot + args.water + args.tolerance) > self.weight > (args.pot + args.tolerance):
            return ScaleStates.COFFEEPOT
        if self.weight > (args.pot + args.water + args.tolerance):
            return ScaleStates.OVERWEIGHT
        return ScaleStates.ERROR

    @property
    def state_str(self):
        return self.state.value

    def __eq__(self, other):
        return self.state_str == other.state_str

    def __str__(self):
        if self.state in [ScaleStates.COFFEEPOT, ScaleStates.OVERWEIGHT]:
            fl_ounces = pounds_to_fl_ounces(self.weight - args.pot)
            return "{} ({})ozs".format(self.state_str, fl_ounces)
        else:
            return '{}'.format(self.state_str)

def control_loop():
    past_states = deque(maxlen=2)
    slack_message_already_sent = False
    while True:
        try:
            curr_state = Reading(get_current_weight())
            past_states.appendleft(curr_state)
            if all([x == curr_state for x in past_states]):
                print('{}'.format(curr_state))
                if curr_state.state == ScaleStates.COFFEEPOT:
                    fl_ounces = pounds_to_fl_ounces(curr_state.weight - args.pot)
                    if not slack_message_already_sent:
                        send_slack_notification(fl_ounces)
                    slack_message_already_sent = True
                else:
                    slack_message_already_sent = False
            else:
                print("Indeterminate state")
        except WeightNotStableError:
            pass
        except RuntimeError as e:
            print(e)
        time.sleep(args.interval)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # TODO: also support reading from stdin
    parser.add_argument('query_exe', type=PosixPath, help='path to query executable')
    parser.add_argument('-p', '--pot', type=float, default=1.75,
                        help="weight (in lbs) of the empty coffee pot")
    parser.add_argument('-w', '--water', type=float, default=2.6,
                        help="maximum amount of water (or coffee) that the pot can hold (in lbs)")
    parser.add_argument('-t', '--tolerance', type=float, default=0.02,
                        help="error tolerance (in lbs)")
    parser.add_argument('-i', '--interval', type=float, default=2,
                        help="monitor weight every X seconds")
    args = parser.parse_args()

    random.seed()

    slack_token = os.environ["SLACK_API_TOKEN"]
    sc = SlackClient(slack_token)

    control_loop()
