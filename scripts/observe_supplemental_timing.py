#!/usr/bin/env python3
"""Explicit 75s receive-only observer in the acceptance container; no raw retention.

Run only under a fresh reviewed hardware plan. This never sends commands and
does not trigger or retry a trial. Arrival timestamps are userspace recv times,
not kernel/hardware timestamps. Compare only in the same container/time namespace.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import socket
import struct
from collections import Counter
from pathlib import Path
from time import monotonic

from sds200.network import UdpDatagramDecoder
from sds200.xml_protocol import ScannerInfoParser, XmlResponseAssembler

DURATION = 75.0
REPLY_LIMIT = 80  # All 60 allowed replies plus bounded unexpected/late replies.
PACKET_LIMIT = 20000
PSI_LIMIT = 400


class Observation:
    def __init__(self, target):
        self.target = ipaddress.IPv4Address(target).packed
        self.decoder = UdpDatagramDecoder(max_sequence_bytes=262144)
        self.decoder.expect_command("PSI,1")  # Local decoding context; not transmitted.
        self.assembler = XmlResponseAssembler(max_bytes=262144)
        self.parser = ScannerInfoParser()
        self.replies = []
        self.screens = Counter()
        self.errors = self.packets = self.psi = 0
        self.stopped = None

    def feed(self, packet, received_at):
        if self.stopped:
            return
        if self.packets >= PACKET_LIMIT:
            self.stopped = "packet_limit"
            return
        self.packets += 1
        if len(packet) < 28 or packet[0] >> 4 != 4 or packet[9] != 17:
            return
        ihl = (packet[0] & 15) * 4
        total = struct.unpack("!H", packet[2:4])[0]
        if (
            ihl < 20
            or total > len(packet)
            or total < ihl + 8
            or struct.unpack("!H", packet[6:8])[0] & 0x3FFF
            or packet[12:16] != self.target
        ):
            return
        sport, _, length = struct.unpack("!HHH", packet[ihl : ihl + 6])
        if sport != 50536 or length < 8 or ihl + length > total:
            return
        body = packet[ihl + 8 : ihl + length]
        content = body.rstrip(b"\r\n")
        if content.startswith((b"FQK,", b"DTM,")):
            if len(self.replies) >= REPLY_LIMIT:
                self.stopped = "reply_limit"
                return
            self.replies.append(
                {
                    "command": content[:3].decode("ascii"),
                    "field_count": content.count(b","),
                    "monotonic_seconds": received_at,
                }
            )
        try:
            for line in self.decoder.feed(body):
                response = self.assembler.feed_with_status(line).response
                if response is None or response[0] not in {"PSI", "GSI"}:
                    continue
                info = self.parser.parse(*response)
                screen = (
                    info.screen
                    if info.screen
                    in {
                        "trunk_scan",
                        "conventional_scan",
                        "analyze_system_status",
                        "close_call",
                        "cc_searching",
                        "wx_alert",
                    }
                    else "other"
                )
                self.psi += 1
                self.screens[(screen, bool(info.records_by_tag("SystemStatus")))] += 1
                if self.psi >= PSI_LIMIT:
                    self.stopped = "psi_limit"
                    return
        except Exception:
            self.errors += 1  # Never retain exception text or unbounded error keys.
            self.assembler.reset()
            self.decoder.reset()
            self.decoder.expect_command("PSI,1")

    def report(self):
        return {
            "schema": 1,
            "stop_reason": self.stopped,
            "packets_observed": self.packets,
            "normal_or_other_psi": self.psi,
            "screens": [
                {"screen": k[0], "system_status": k[1], "count": v}
                for k, v in sorted(self.screens.items())
            ],
            "decode_errors": self.errors,
            "incoming_reply_counts": dict(Counter(r["command"] for r in self.replies)),
            "replies": [dict(r) for r in self.replies],
            "reply_limit": REPLY_LIMIT,
            "collector_commands_sent": 0,
            "outgoing_wire_delivery_established": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe-75s", action="store_true", required=True)
    parser.parse_args()
    logging.disable(logging.CRITICAL)
    options = json.loads(Path("/data/options.json").read_text())
    observation = Observation(options["scanner_host"])
    del options
    with socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800)) as sock:
        sock.bind(("eth0", 0))
        started = monotonic()
        print(
            json.dumps({"capture_ready": True, "schema": 1, "start_monotonic_seconds": started}),
            flush=True,
        )
        while not observation.stopped:
            remaining = started + DURATION - monotonic()
            if remaining <= 0:
                break
            sock.settimeout(min(0.5, remaining))
            try:
                packet = sock.recv(65535)
            except TimeoutError:
                continue
            received_at = monotonic()
            if received_at >= started + DURATION:
                break
            observation.feed(packet, received_at)
    print(
        json.dumps(
            {
                "summary": {
                    **observation.report(),
                    "complete": observation.stopped is None,
                    "clock": "time.monotonic (same container required for comparison)",
                    "start_monotonic_seconds": started,
                    "elapsed_seconds": monotonic() - started,
                }
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
