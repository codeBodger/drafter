from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Optional, Union
import difflib
try:
    import bakery
except:
    bakery = None

import logging
logger = logging.getLogger('drafter')

@dataclass
class BakeryTestCase:
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    result: Any
    line: Optional[int]
    caller: Optional[str]


DEFAULT_STACK_DEPTH = 7
def get_line_code(depth: int = DEFAULT_STACK_DEPTH) -> Union[tuple[int, Union[str, None]], tuple[None, None]]:
    # Load in extract_stack, or provide shim for environments without it.
    try:
        from traceback import extract_stack
        trace = extract_stack()
        # Find the first assert_equal line
        for data in trace:
            line, code = data[1], data[3]
            if (code or "").strip().startswith('assert_equal'):
                return line, code
        # If none found, just try jumping up there and see what we can find
        frame = trace[len(trace) - depth]
        line = frame[1]
        code = frame[3]
        return line, code
    except Exception as e:
        # logger.error(f"Error getting line and code: {e}")
        return None, None


class BakeryTests:
    def __init__(self) -> None:
        self.tests: list[BakeryTestCase] = []

    def wrap_get_line_code(self, original_function: Callable[[], Union[tuple[int, Union[str, None]], tuple[None, None]]]) -> Callable[[Optional[int]], Union[tuple[int, Union[str, None]], tuple[None, None]]]:
        @wraps(original_function)
        def new_function(*args: Any, **kwargs: Any) -> Union[tuple[int, Union[str, None]], tuple[None, None]]:
            # line, code = original_function(*args, **kwargs)
            # return line, code
            return get_line_code()
        return new_function

    def track_bakery_tests(self, original_function: Callable[..., bool]) -> Callable[..., bool]:
        if bakery is None:
            return original_function
        @wraps(original_function)
        def new_function(*args: Any, **kwargs: Any) -> bool:
            line, code = get_line_code(6)
            result = original_function(*args, **kwargs)
            self.tests.append(BakeryTestCase(args, kwargs, result, line, code))
            return result

        return new_function


# Modifies Bakery's copy of assert_equal, and also provides a new version for folks who already imported
_bakery_tests = BakeryTests()
if bakery is not None:
    bakery.assertions.get_line_code = _bakery_tests.wrap_get_line_code(bakery.assertions.get_line_code)
    bakery.assert_equal = assert_equal = _bakery_tests.track_bakery_tests(bakery.assert_equal)
else:
    def assert_equal(*args: Any, **kwargs: Any) -> bool:
        """ Pointless definition of assert_equal to avoid errors """
        print("The Bakery testing library is not installed; skipping assert_equal tests. "
              "To fix this, you can install Bakery with 'pip install bakery' or use a different testing framework.")
        return False


DIFF_INDENT_WIDTH = 1
DIFF_WRAP_WIDTH = 60
differ = difflib.HtmlDiff(tabsize=DIFF_INDENT_WIDTH, wrapcolumn=DIFF_WRAP_WIDTH)

def diff_tests(left: str, right: str, left_name: str, right_name: str) -> str:
    """ Compare two strings and show the differences in a table. """
    try:
        table = differ.make_table(left.splitlines(), right.splitlines(), left_name, right_name)
        return table
    except:
        if left == right:
            return "No differences found."
        return f"<pre>{left}</pre><pre>{right}</pre>"
