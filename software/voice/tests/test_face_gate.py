#!/usr/bin/env python3
"""The face door client (UDP, a fake door on a free port) and the show interlock.

    python3 software/voice/tests/test_face_gate.py
"""
import asyncio
import socket
import unittest

import fakes  # noqa: F401

import face
import gate


class FakeDoor(asyncio.DatagramProtocol):
    def __init__(self):
        self.lines = []

    def connection_made(self, t):
        self.t = t

    def datagram_received(self, data, addr):
        line = data.decode()
        self.lines.append(line)
        self.t.sendto(f"OK {line.split()[0].lower()}".encode(), addr)


def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Door(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        loop = asyncio.get_running_loop()
        self.transport, self.door = await loop.create_datagram_endpoint(FakeDoor, local_addr=("127.0.0.1", 0))
        self.port = self.transport.get_extra_info("sockname")[1]

    async def asyncTearDown(self):
        self.transport.close()

    async def test_send_and_reply(self):
        f = face.FaceDoor(port=self.port, convo=False)
        self.assertEqual(await f.send("EMOTE SOFT"), "OK emote")
        self.assertEqual(self.door.lines, ["EMOTE SOFT"])

    async def test_refuses_before_sending(self):
        f = face.FaceDoor(port=self.port, convo=False)
        with self.assertRaises(face.FaceRefused):
            await f.send("GAZE 0 0")
        self.assertEqual(self.door.lines, [])

    async def test_convo_is_off_until_agreed(self):
        f = face.FaceDoor(port=self.port)          # no OPENDUBS_VOICE_CONVO in the test env
        self.assertFalse(f.convo_enabled)
        await f.convo("listen")
        await asyncio.sleep(0.05)
        self.assertEqual(self.door.lines, [])

    async def test_convo_lease_is_renewed_then_released(self):
        old = face.CONVO_RENEW_S
        face.CONVO_RENEW_S = 0.05
        try:
            f = face.FaceDoor(port=self.port, convo=True)
            await f.convo("think")
            await asyncio.sleep(0.18)
            await f.convo("idle")
            await asyncio.sleep(0.1)
        finally:
            face.CONVO_RENEW_S = old
        self.assertEqual(self.door.lines[0], "CONVO THINK 3000")
        self.assertGreaterEqual(self.door.lines.count("CONVO THINK 3000"), 3)
        self.assertEqual(self.door.lines[-1], "CONVO IDLE")

    async def test_no_door_answers_err_not_an_exception(self):
        f = face.FaceDoor(port=free_udp_port(), convo=False)
        self.assertTrue((await f.send("MOVE blink2")).startswith("ERR"))


class Interlock(unittest.IsolatedAsyncioTestCase):
    def test_verdicts(self):
        self.assertEqual(gate.verdict(200, {"running": None}), (True, None))
        self.assertEqual(gate.verdict(200, {"running": "wave"}), (False, "a show is running (wave)"))
        self.assertEqual(gate.verdict(503, {"error": "the show runner is not running"}), (True, None))
        for code, body in ((0, None), (500, None), (200, None), (200, "x"), (404, {})):
            on, why = gate.verdict(code, body)
            self.assertFalse(on, (code, body))
            self.assertIn("can't confirm", why)

    async def test_changes_reach_listeners_once(self):
        answers = [(200, {"running": None}), (200, {"running": None}), (200, {"running": "bow"}), (0, None)]
        g = gate.Gate(fetch=lambda url: answers.pop(0), enabled=True)
        seen = []

        async def cb(on, why):
            seen.append((on, why))
        g.listeners.append(cb)
        for _ in range(4):
            await g.check()
        self.assertEqual(seen, [(True, None), (False, "a show is running (bow)"),
                                (False, "can't confirm no show is running (the dashboard did not answer)")])

    async def test_switched_off(self):
        g = gate.Gate(fetch=lambda url: (200, {"running": None}), enabled=False)
        self.assertEqual(await g.check(), (False, "voice is switched off on this robot"))

    async def test_a_failing_check_closes_voice(self):
        def boom(url):
            raise RuntimeError("bug")
        g = gate.Gate(fetch=boom, enabled=True)
        g.on = True
        old = gate.POLL_S
        gate.POLL_S = 0.01
        try:
            t = asyncio.create_task(g.run())
            await asyncio.sleep(0.05)
            t.cancel()
        finally:
            gate.POLL_S = old
        self.assertFalse(g.on)


if __name__ == "__main__":
    unittest.main()
