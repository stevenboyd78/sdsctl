#!/usr/bin/env python3
"""Uninstalled original-outer passive completion; no launch or active admission.

Borrow already qualified original peers, an already armed original Watch,
authenticated Inputs, independently reviewed CasePlan and pre-staged listeners.
Join final-plan transport, startup publication and passive descriptor release.
Installation/runtime provenance and independently bounded initial startup remain
caller obligations. Neither this library nor a returned receipt grants App work,
recording, recovery, observed peer exit or successful watcher retirement.
"""

from __future__ import annotations

import math
import os
import select
import time
from dataclasses import dataclass

import supplemental_recording_peer_delivery as delivery
import supplemental_recording_peer_preparation as preparation
import supplemental_recording_service_submit as submission

require = delivery.require


@dataclass(frozen=True)
class Completed:
    """Passive transport facts only; original caller still observes all exits."""

    plan_receipt: str
    delivered: delivery.Delivered


def complete(
    custody,
    watch,
    local,
    inputs,
    original,
    plan_listener,
    writer_listener,
    observer_listener,
    *,
    deadline,
):
    """One attempt, existing owners/cutoffs, no process or listener construction.

    Only the exact fixed passive writer/observer pair is allowed. After binding
    the original custody/watch and consuming this pair's slot, any uncertainty
    cancels THAT watcher; unrelated unbound objects are never closed. Case files
    survive every outcome. Full delivery comparisons remain fresh and unchanged.
    The caller retains every borrowed object, and must independently supervise
    blocked I/O, initial startup and the outer's entire lifetime.
    """
    accepted, problem, result = False, None, None
    try:
        require(type(custody) is delivery.termination.Custody)
        require(type(watch) is delivery.termination.Watch)
        custody._guard()
        watch._guard()
        require(custody.armed_watch is watch and custody.attempted and not watch.finished)
        require(watch.identities is custody.identities and watch.deadline_ns == custody.deadline_ns)
        require(not select.select([watch.fd], [], [], 0)[0])
        pair = custody.pair
        require(type(pair) is delivery.termination.peers.PeerRuntimePair)
        require(
            pair.passive_completion_attempted is False and pair.channel_delivery_attempted is False
        )
        pair.passive_completion_attempted = True
        accepted = True
        require(type(deadline) in (int, float) and math.isfinite(deadline))
        end = min(deadline, custody.plan.lease["ready_by"])
        require(time.monotonic() < end)
        writer, observer, plan, clock = pair.writer, pair.observer, custody.plan, custody.clock
        require(
            type(local) is preparation.links.processes.ProcessIdentity and local.pid == os.getpid()
        )
        require(type(inputs) is preparation.inputs_module.Inputs)
        require(type(original) is submission.intake.CasePlan)
        require(observer.passive_observer is True and observer.observer_outer is local)
        baseline = observer.observer_baseline_sha256
        command = (
            "/usr/local/bin/python",
            "-I",
            "-B",
            preparation.ENTRYPOINT,
            plan.case,
            inputs.template.sha256,
            baseline,
            f"{local.pid}:{local.start_ticks}:{local.container_id}",
        )
        require(writer.command == (*command, preparation.RETAINED_MODE))
        require(observer.command == (*command, preparation.OBSERVER_MODE))
        require(len({id(plan_listener), id(writer_listener), id(observer_listener)}) == 3)
        retained = (plan_listener, writer_listener, observer_listener)
        for listener, witness in zip(
            retained, (observer.witness, writer.witness, observer.witness), strict=True
        ):
            require(type(listener) is preparation.listeners.Listener)
            require(listener.peer is witness and listener.accepted is False)
            require(listener.deadline <= deadline)
            end = min(end, listener.deadline)
        # Phase APIs enforce their borrowed listener's cutoff. Do not relabel a
        # later listener as the smaller original whole-sequence limit: the
        # caller must provision all three with that SAME original cutoff.
        require(all(listener.deadline == end for listener in retained))
        pinned = original.recheck()
        require(pinned.raw == plan.raw and pinned.sha256 == plan.sha256)
        owners = (pair, plan, clock, writer, observer, inputs.template, inputs.expectations)

        def guard():
            require(time.monotonic() < end)
            require(
                all(
                    current is previous
                    for current, previous in zip(
                        (
                            custody.pair,
                            custody.plan,
                            custody.clock,
                            pair.writer,
                            pair.observer,
                            inputs.template,
                            inputs.expectations,
                        ),
                        owners,
                        strict=True,
                    )
                )
            )
            require(pair.passive_completion_attempted is True)
            require(custody.armed_watch is watch and not watch.finished)
            custody._guard()
            custody._live(end)
            watch._guard()
            require(not select.select([watch.fd], [], [], 0)[0])
            pair._guard(end)
            require(inputs.template is writer.template and inputs.template is observer.template)
            require(
                inputs.expectations is writer.expectations
                and inputs.expectations is observer.expectations
            )
            require(inputs.expected == writer.expectations_sha256 == observer.expectations_sha256)
            require(inputs.recheck(deadline=end) is inputs.expectations)
            require(original.recheck() is pinned and pinned.raw == plan.raw)
            for listener in retained:
                listener.recheck()
            require(time.monotonic() < end)

        guard()
        plan_listener.accept()
        guard()
        receipt = preparation.send_observer_plan(
            inputs,
            original,
            plan_listener,
            clock,
            local,
            baseline_sha256=baseline,
            counterpart=writer.witness,
        )
        guard()
        submission.Submission(original, inputs.template.sha256, plan.sha256).submit_before(end)
        guard()
        writer_listener.accept()
        guard()
        observer_listener.accept()
        guard()
        delivered = delivery.deliver_and_release_passive_writer(
            custody, watch, local, inputs, writer_listener, observer_listener
        )
        # Release can cause immediate writer exit. No post-release live-peer
        # guard or promise of observed retirement is valid here.
        result = Completed(receipt, delivered)
    except BaseException as error:
        problem = error
    if problem is not None:
        if accepted:
            try:
                watch.close()
            except BaseException as cleanup:
                if not isinstance(cleanup, Exception):
                    problem = cleanup
        if not isinstance(problem, Exception):
            raise problem
        raise delivery.UnconfirmedDelivery(delivery.MESSAGE) from None
    return result


if __name__ == "__main__":
    raise SystemExit("Uninstalled passive outer library only; no launch or App action enabled.")
