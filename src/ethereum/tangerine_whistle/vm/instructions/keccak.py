"""
Ethereum Virtual Machine (EVM) Keccak Instructions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

Implementations of the EVM keccak instructions.
"""

from ethereum.base_types import U256, Uint
from ethereum.crypto.hash import keccak256
from ethereum.utils.numeric import ceil32
from ethereum.utils.safe_arithmetic import u256_safe_add, u256_safe_multiply

from ...vm.error import OutOfGasError
from .. import Evm
from ..gas import GAS_KECCAK256, GAS_KECCAK256_WORD, subtract_gas
from ..memory import memory_read_bytes
from ..stack import pop, push


def keccak(evm: Evm) -> None:
    """
    Pushes to the stack the Keccak-256 hash of a region of memory.

    This also expands the memory, in case the memory is insufficient to
    access the data's memory location.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.tangerine_whistle.vm.error.StackUnderflowError`
        If `len(stack)` is less than `2`.
    """
    memory_start_index = pop(evm.stack)
    size = pop(evm.stack)

    words = ceil32(Uint(size)) // 32
    word_gas_cost = u256_safe_multiply(
        GAS_KECCAK256_WORD,
        words,
        exception_type=OutOfGasError,
    )
    total_gas_cost = u256_safe_add(
        GAS_KECCAK256,
        word_gas_cost,
        exception_type=OutOfGasError,
    )
    subtract_gas(evm, total_gas_cost)

    data = memory_read_bytes(evm, memory_start_index, size)
    hash = keccak256(data)

    push(evm.stack, U256.from_be_bytes(hash))

    evm.pc += 1
