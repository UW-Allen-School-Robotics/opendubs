"""The robot's name, shown on every dashboard so a tablet never drives the wrong robot."""
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import metrics


def test_the_robots_own_setting_wins(monkeypatch):
    monkeypatch.setenv("OPENDUBS_ROBOT_NAME", "  OpenDubs 2 ")
    assert metrics.robot_name() == "OpenDubs 2"


def test_without_a_setting_it_is_the_hostname(monkeypatch):
    monkeypatch.delenv("OPENDUBS_ROBOT_NAME", raising=False)
    assert metrics.robot_name() == socket.gethostname().split(".")[0]


def test_an_empty_setting_falls_back_to_the_hostname(monkeypatch):
    monkeypatch.setenv("OPENDUBS_ROBOT_NAME", "")
    assert metrics.robot_name() == socket.gethostname().split(".")[0]
