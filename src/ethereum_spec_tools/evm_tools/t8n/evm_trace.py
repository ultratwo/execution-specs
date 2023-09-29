"""
The module implements the raw EVM tracer for t8n.
"""
import json
import os
from dataclasses import dataclass, fields
from typing import List, Optional, Protocol, TextIO, Union, runtime_checkable

from ethereum.base_types import U256, Bytes, Uint
from ethereum.trace import (
    EvmStop,
    GasAndRefund,
    OpEnd,
    OpException,
    OpStart,
    PrecompileEnd,
    PrecompileStart,
    TraceEvent,
    TransactionEnd,
    TransactionStart,
)

EXCLUDE_FROM_OUTPUT = ["gasCostTraced", "errorTraced", "precompile"]


@dataclass
class Trace:
    """
    The class implements the raw EVM trace.
    """

    pc: int
    op: str
    gas: str
    gasCost: str
    memory: Optional[str]
    memSize: int
    stack: Optional[List[str]]
    returnData: Optional[str]
    depth: int
    refund: int
    opName: str
    gasCostTraced: bool = False
    errorTraced: bool = False
    precompile: bool = False
    error: Optional[str] = None


@dataclass
class FinalTrace:
    """
    The class implements final trace for a tx.
    """

    output: str
    gasUsed: str
    error: Optional[str] = None

    def __init__(self, gas_used: int, output: bytes, has_erred: bool) -> None:
        self.output = output.hex()
        self.gasUsed = hex(gas_used)
        if has_erred:
            self.error = ""


@runtime_checkable
class Environment(Protocol):
    """
    The class implements the environment interface for trace.
    """

    traces: List[Union["Trace", "FinalTrace"]]


@runtime_checkable
class Message(Protocol):
    """
    The class implements the message interface for trace.
    """

    depth: int
    parent_evm: Optional["Evm"]


@runtime_checkable
class Evm(Protocol):
    """
    The class implements the EVM interface for trace.
    """

    pc: Uint
    stack: List[U256]
    memory: bytearray
    code: Bytes
    gas_left: Uint
    env: Environment
    refund_counter: int
    running: bool
    message: Message
    return_data: Bytes


def evm_trace(
    evm: object,
    event: TraceEvent,
    trace_memory: bool = False,
    trace_stack: bool = True,
    trace_return_data: bool = False,
) -> None:
    """
    Create a trace of the event.
    """
    assert isinstance(evm, Evm)

    last_trace = None
    if evm.env.traces:
        last_trace = evm.env.traces[-1]

    refund_counter = evm.refund_counter
    parent_evm = evm.message.parent_evm
    while parent_evm is not None:
        refund_counter += parent_evm.refund_counter
        parent_evm = parent_evm.message.parent_evm

    len_memory = len(evm.memory)

    return_data = None
    if trace_return_data and evm.return_data:
        return_data = "0x" + evm.return_data.hex()

    memory = None
    if trace_memory and len_memory > 0:
        memory = "0x" + evm.memory.hex()

    stack = None
    if trace_stack:
        stack = [hex(i) for i in evm.stack]

    if isinstance(event, TransactionStart):
        pass
    elif isinstance(event, TransactionEnd):
        final_trace = FinalTrace(event.gas_used, event.output, event.has_erred)
        evm.env.traces.append(final_trace)
    elif isinstance(event, PrecompileStart):
        new_trace = Trace(
            pc=evm.pc,
            op="0x" + event.address.hex().lstrip("0"),
            gas=hex(evm.gas_left),
            gasCost="0x0",
            memory=memory,
            memSize=len_memory,
            stack=stack,
            returnData=return_data,
            depth=evm.message.depth + 1,
            refund=refund_counter,
            opName="0x" + event.address.hex().lstrip("0"),
            precompile=True,
        )

        evm.env.traces.append(new_trace)
    elif isinstance(event, PrecompileEnd):
        assert isinstance(last_trace, Trace)

        last_trace.gasCostTraced = True
        last_trace.errorTraced = True
    elif isinstance(event, OpStart):
        new_trace = Trace(
            pc=evm.pc,
            op=event.op.value,
            gas=hex(evm.gas_left),
            gasCost="0x0",
            memory=memory,
            memSize=len_memory,
            stack=stack,
            returnData=return_data,
            depth=evm.message.depth + 1,
            refund=refund_counter,
            opName=str(event.op).split(".")[-1],
        )

        evm.env.traces.append(new_trace)
    elif isinstance(event, OpEnd):
        assert isinstance(last_trace, Trace)

        last_trace.gasCostTraced = True
        last_trace.errorTraced = True
    elif isinstance(event, OpException):
        if last_trace is not None:
            assert isinstance(last_trace, Trace)
        if (
            # The first opcode in the code is an InvalidOpcode.
            # So we add a new trace with InvalidOpcode as op.
            not last_trace
            # The current opcode is an InvalidOpcode. This condition
            # is true if an InvalidOpcode is found in any location
            # other than the first opcode.
            or last_trace.errorTraced
            # The first opcode in a child message is an InvalidOpcode.
            # This case has to be explicitly handled since the first
            # two conditions do not cover it.
            or last_trace.depth == evm.message.depth
        ):
            new_trace = Trace(
                pc=evm.pc,
                op="InvalidOpcode",
                gas=hex(evm.gas_left),
                gasCost="0x0",
                memory=memory,
                memSize=len_memory,
                stack=stack,
                returnData=return_data,
                depth=evm.message.depth + 1,
                refund=refund_counter,
                opName="InvalidOpcode",
                gasCostTraced=True,
                errorTraced=True,
                error="",
            )

            evm.env.traces.append(new_trace)
        elif not last_trace.errorTraced:
            # If the error for the last trace is not covered
            # the exception is attributed to the last trace.
            last_trace.error = ""
            last_trace.errorTraced = True
    elif isinstance(event, EvmStop):
        if not evm.running:
            return
        elif len(evm.code) == 0:
            return
        else:
            evm_trace(
                evm,
                OpStart(event.op),
                trace_memory,
                trace_stack,
                trace_return_data,
            )
    elif isinstance(event, GasAndRefund):
        if not evm.env.traces:
            # In contract creation transactions, there may not be any traces
            return

        assert isinstance(last_trace, Trace)

        if not last_trace.gasCostTraced:
            last_trace.gasCost = hex(event.gas_cost)
            last_trace.refund = refund_counter
            last_trace.gasCostTraced = True


def output_op_trace(
    trace: Union[Trace, FinalTrace], json_file: TextIO
) -> None:
    """
    Output a single trace to a json file.
    """
    dict_trace = {
        field.name: getattr(trace, field.name)
        for field in fields(trace)
        if field.name not in EXCLUDE_FROM_OUTPUT
        and getattr(trace, field.name) is not None
    }

    json.dump(dict_trace, json_file, separators=(",", ":"))
    json_file.write("\n")


def output_traces(
    traces: List[Union[Trace, FinalTrace]],
    tx_index: int,
    tx_hash: bytes,
    output_basedir: str = ".",
) -> None:
    """
    Output the traces to a json file.
    """
    tx_hash_str = "0x" + tx_hash.hex()
    output_path = os.path.join(
        output_basedir, f"trace-{tx_index}-{tx_hash_str}.jsonl"
    )
    with open(output_path, "w") as json_file:
        for trace in traces:

            if getattr(trace, "precompile", False):
                # Traces related to pre-compile are not output.
                continue
            output_op_trace(trace, json_file)
