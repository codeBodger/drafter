from functools import partial
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
        return Redirect(state, Route(self.func))

class RedirectRoute(Route[REDIRECT]):
    def __call__(self, *args: Any, **kwargs: Any) -> Callable[[], 'Redirect']:
        return partial(self.func, *args, **kwargs)


@overload
def route(url: ROUTE, server: Optional[Server] = None) -> TrueRoute[ROUTE]: ...

@overload
def route(url: REDIRECT, server: Optional[Server] = None) -> RedirectRoute[REDIRECT]: ...

@overload
def route(url: Optional[str] = None, server: Optional[Server] = None) -> Callable[
        [Union[ROUTE, REDIRECT]],
        Union[TrueRoute[ROUTE], RedirectRoute[REDIRECT]]
    ]: ...

def route(url: Union[ROUTE, REDIRECT, str, None] = None, server: Optional[Server] = None) -> Union[
        TrueRoute[ROUTE], RedirectRoute[REDIRECT],
        Callable[[Union[ROUTE, REDIRECT]], Union[TrueRoute[ROUTE], RedirectRoute[REDIRECT]]]
    ]:
    """
    Main function to add a new route to the server. Recommended to use as a decorator.
    Once added, the route will be available at the given URL; the function name will be used if no URL is provided.
    When you go to the URL, the function will be called and its return value will be displayed.

    Note: No arguments beyond the state may be passed to routes.  Routes should not be
    called directly.  

    :param url: The URL to add the route to. If None, the function name will be used.
    :param server: The server to add the route to. Defaults to the main server.
    :return: The modified route function.
    """
    server = server or get_main_server()
    if callable(url):
        local_url = url.__name__
        server.add_route(local_url, url)

        if is_true_route(url):
            return TrueRoute(url)
        elif is_redirect_route(url): # should just be able to be an else, but Pylance isn't not happy
            return RedirectRoute(url)

    def make_route(func: Union[ROUTE, REDIRECT]) -> Union[TrueRoute[ROUTE], RedirectRoute[REDIRECT]]:
        local_url = url
        if local_url is None:
            local_url = func.__name__
        server.add_route(local_url, func)

        if is_true_route(func):
            return TrueRoute(func)
        elif is_redirect_route(func): # should just be able to be an else, but Pylance isn't not happy
            return RedirectRoute(func)

    return make_route

def is_true_route(func: Callable[..., Any]) -> 'TypeIs[Callable[[Any], Page]]':
        annotations = set(func.__annotations__)
        annotations.discard("return")
        return len(annotations) == 1 # there is only one parameter, the state
def is_redirect_route(func: Callable[..., Any]) -> 'TypeIs[Callable[Concatenate[Any, Any, ...], Redirect]]':
        annotations = set(func.__annotations__)
        annotations.discard("return")
        return len(annotations) > 1 # there is more than one parameter, more than just the state
