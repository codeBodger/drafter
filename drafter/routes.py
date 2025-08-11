from functools import partial
from inspect import Parameter, signature
from typing import Any, Concatenate, Generic, TypeVar, Union, Callable, Optional, TYPE_CHECKING, overload
from drafter.server import Server, get_main_server

if TYPE_CHECKING:
    from drafter.page import Page, Redirect
    from typing_extensions import TypeIs


FUNC = TypeVar('FUNC', bound=Callable[Concatenate[Any, ...], 'Page'], covariant=True)
ROUTE = TypeVar('ROUTE', bound=Callable[[Any], 'Page'])
REDIRECT = TypeVar('REDIRECT', bound=Callable[Concatenate[Any, Any, ...], 'Redirect'])

class Route(Generic[FUNC]):
    def __init__(self, func: FUNC) -> None:
        self.func = func
    
    def __getattr__(self, attr: str) -> Any:
        return getattr(self.func, attr)

class TrueRoute(Route[ROUTE]):    
    def __call__(self, state: Any) -> 'Redirect':
        from drafter.page import Redirect
        return Redirect(state, Route(self.func))

class RedirectRoute(Route[REDIRECT]):
    def __call__(self, *args: Any, **kwargs: Any) -> Callable[[], 'Redirect']:
        return partial(self.func, *args, **kwargs)


@overload
def route(func_or_route_name: ROUTE, server: Optional[Server] = None) -> TrueRoute[ROUTE]: ...

@overload
def route(func_or_route_name: REDIRECT, server: Optional[Server] = None) -> RedirectRoute[REDIRECT]: ...

@overload
def route(func_or_route_name: Optional[str] = None, server: Optional[Server] = None) -> Callable[
        [Union[ROUTE, REDIRECT]],
        Union[TrueRoute[ROUTE], RedirectRoute[REDIRECT]]
    ]: ...

def route(func_or_route_name: Union[ROUTE, REDIRECT, str, None] = None, server: Optional[Server] = None) -> Union[
        TrueRoute[ROUTE], RedirectRoute[REDIRECT],
        Callable[[Union[ROUTE, REDIRECT]], Union[TrueRoute[ROUTE], RedirectRoute[REDIRECT]]]
    ]:
    """
    Main function to add a new route to the server. Recommended to use as a decorator.
    Once added, the route will be available with the given name; the function name will
    be used if none is provided. When you use the name (in a button or similar), the
    function will be called and its return value will be displayed.

    TODO: Discuss this
    Note: If a `route` has required parameter beyond the state,
        it must return a `Redirect`.

    :param func_or_route_name: The name to use for the route.
        If None, the function name will be used.
    :param server: The server to add the route to. Defaults to the main server.
    :return: The modified route function.
    """
    server = server or get_main_server()
    if callable(func_or_route_name):
        func = func_or_route_name
        name = func.__name__
        server.add_route(name, func)

        if is_true_route(func):
            return TrueRoute(func)
        elif is_redirect_route(func): # should just be able to be an else, but Pylance isn't not happy
            return RedirectRoute(func)
        else:
            raise ValueError(f"The route `{name}` could not be added due to bad arguments or typing.")

    def make_route(func: Union[ROUTE, REDIRECT]) -> Union[TrueRoute[ROUTE], RedirectRoute[REDIRECT]]:
        name = func_or_route_name
        if name is None:
            name = func.__name__
        server.add_route(name, func)

        if is_true_route(func):
            return TrueRoute(func)
        elif is_redirect_route(func): # should just be able to be an else, but Pylance isn't not happy
            return RedirectRoute(func)

    return make_route

def is_true_route(func: Callable[..., Any]) -> 'TypeIs[Callable[[Any], Page]]':
        return req_param_count(func) == 1 # there is only one parameter, the state
def is_redirect_route(func: Callable[..., Any]) -> 'TypeIs[Callable[Concatenate[Any, Any, ...], Redirect]]':
        return req_param_count(func) > 1 # there is more than one parameter, more than just the state
SINGLE_PARAM = [Parameter.KEYWORD_ONLY, Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD]
def req_param_count(func: Callable[..., Any]) -> int:
    sig_params = list(signature(func).parameters.values())
    single_params = [param.name for param in sig_params if param.kind in SINGLE_PARAM]
    return len(single_params) - len(func.__defaults__ or [])
