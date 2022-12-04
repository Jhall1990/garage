import os
import time
import yaml
import argparse
import threading
import RPi.GPIO as gpio

from influxdb import InfluxDBClient
from flask import Flask, request, make_response


app = Flask("garage")

# Disable secondary mode for all gpio pins
gpio.setmode(gpio.BOARD)


DOOR_STATE_OPEN = "open"
DOOR_STATE_CLOSED = "closed"
DOOR_STATE_MOVING = "moving"


# Global variable that store the path to the config
# file. We use a global so we can access it in the
# flask app routes.
CONFIG_PATH = None


###########
# Classes #
###########
class Door():
    def __init__(self, open_pin, closed_pin, relay_pin):
        self.open_pin = open_pin
        self.closed_pin = closed_pin
        self.relay_pin = relay_pin
        self.configure_pins()

    def configure_pins(self):
        gpio.setup(self.open_pin, gpio.IN, gpio.PUD_UP)
        gpio.setup(self.closed_pin, gpio.IN, gpio.PUD_UP)
        gpio.setup(self.relay_pin, gpio.OUT)
        gpio.output(self.relay_pin, True)

    def state(self):
        if gpio.input(self.open_pin):
            return DOOR_STATE_OPEN
        if gpio.input(self.closed_pin):
            return DOOR_STATE_CLOSED
        return DOOR_STATE_MOVING

    def open_close(self):
        gpio.output(self.relay_pin, False)
        time.sleep(2)
        gpio.output(self.relay_pin, True)

    @staticmethod
    def from_cfg(cfg):
        return Door(cfg["open_pin"],
                    cfg["closed_pin"],
                    cfg["relay_pin"])


###########
# Helpers #
###########
def doors_from_yaml(config_path):
    with open(config_path, "r", encoding="utf-8") as config:
        config_yaml = yaml.safe_load(config)
    return {i: Door.from_cfg(config_yaml["doors"][i]) for i in config_yaml["doors"]}


def get_state_for_doors(doors):
    return {d: doors[d].state() for d in doors}


def write_door_state_to_influx(state):
    host = os.environ["INFLUX_HOST"]
    port = int(os.environ["INFLUX_PORT"])
    db = os.environ["INFLUX_DB"]
    client = InfluxDBClient(host, port, database=db)
    points = []

    for name, door_state in state.items():
        point = {
            "measurement": "garage",
            "tags": {"name": name},
            "fields": {"status": door_state}
        }
        points.append(point)
    client.write_points(points)


def start_state_thread(config, interval):
    def state():
        while True:
            time.sleep(interval)
            doors = doors_from_yaml(config)
            door_state = get_state_for_doors(doors)
            write_door_state_to_influx(door_state)
    t = threading.Thread(target=state)
    t.daemon = True
    t.start()


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", required=True,
                    help="The path to the config file")
    ap.add_argument("-i", "--interval",required=True,
                    help="Interval between door state stat collection")
    return ap.parse_args()


##########
# Routes #
##########
@app.route("/open_close/<name>", methods=["GET"])
def open_close_door(name):
    doors = doors_from_yaml(CONFIG_PATH)
    door = doors.get(name)

    if door:
        door.open_close()
        result = True
    else:
        result = False

    # All this because CORS is an asshole
    resp = make_response({"result": True})
    resp.headers["Access-Control-Allow-Origin"] = request.environ["HTTP_ORIGIN"]
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


if __name__ == "__main__":
    opts = parse_args()
    CONFIG_PATH = opts.config

    # Start the stat collector thread
    start_state_thread(CONFIG_PATH, int(opts.interval))

    # Start the rest server
    app.run(host="0.0.0.0")
