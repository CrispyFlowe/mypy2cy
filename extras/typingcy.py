
from mypy2cy import type_params, convert_type

from typing import (
    Literal, # type:ignore
    TypeVar, 
    Generic, 
    List as _List
)

### BEGIN NATIVE CYTHON DEFINITIONS ###

_ElType = TypeVar("_ElType")
_ArSize = TypeVar("_ArSize")

@type_params("Type")
@convert_type("{Type}[:]")
class memory_View(Generic[_ElType]):
    pass

@type_params("Type")
@convert_type("{Type}[:]")
class mv_List(list[_ElType], Generic[_ElType]):
    """ @cython_typed \n
    a dynamic memory-view list, in native cython \n
    >>> arr: mv_List[int] = ...
    >> cdef int[:] arr = ... (cython)
    """

@type_params("ArrayType", "Size")
@convert_type("{ArrayType}[{Size}]")
class static_List(list[_ElType], Generic[_ElType, _ArSize]):
    """ @cython_typed \n
    a static memory-view list, in native cython, 
    fixed in its size \n
    >>> arr: static_List[int, 500] = ...
    >> cdef int[500] arr = ... (cython)
    """

### END NATIVE CYTHON DEFINITIONS ###
