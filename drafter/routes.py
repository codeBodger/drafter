from typing import Any, Concatenate, Generic, TypeVar, Union, Callable, Optional, TYPE_CHECKING, overload
from drafter.server import Server, get_main_server

if TYPE_CHECKING:
    from drafter.page import Page, Redirect


FUNC = TypeVar('FUNC', bound=Callable[Concatenate[Any, ...], 'Page'], covariant=True)

class Route(Generic[FUNC]):
    def __init__(self, func: FUNC) -> None:
        self.func = func
    
    def __getattr__(self, attr: str) -> Any:
        return getattr(self.func, attr)



ROUTE = TypeVar('ROUTE', bound=Callable[[Any], 'Page'])

@overload
def route(url: ROUTE, server: Optional[Server] = None) -> Route[ROUTE]: ...
@overload
def route(url: Optional[str] = None, server: Optional[Server] = None) -> Callable[
    [ROUTE],
    Route[ROUTE]
]: ...

def route(url: Union[ROUTE, str, None] = None, server: Optional[Server] = None) -> Union[
        Route[ROUTE],
        Callable[[ROUTE], Route[ROUTE]]
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
        return Route(url)

    def make_route(func: ROUTE) -> Route[ROUTE]:
        local_url = url
        if local_url is None:
            local_url = func.__name__
        server.add_route(local_url, func)
        return Route(func)

    return make_route


REDIRECT = TypeVar('REDIRECT', bound=Callable[Concatenate[Any, ...], 'Redirect'])

@overload
def redirect(url: REDIRECT, server: Optional[Server] = None) -> Route[REDIRECT]: ...
@overload
def redirect(url: Optional[str] = None, server: Optional[Server] = None) -> Callable[
        [REDIRECT],
        Route[REDIRECT]
    ]: ...

def redirect(url: Union[REDIRECT, str, None] = None, server: Optional[Server] = None) -> Union[
        Route[REDIRECT],
        Callable[[REDIRECT], Route[REDIRECT]]
    ]:
    """
    Main function to add a new redirect to the server. Recommended to use as a decorator.
    A redirect allows arguments in addition to the state to be passed, but does not
    create a Page to be seen, in the same way as a route. Instead, a Redirect indicating
    the route to procede to must be returned.

    :param url: The URL to add the redirect to. If None, the function name will be used.
    :param server: The server to add the redirect to. Defaults to the main server.
    :return: The modified redirect function.
    """
    server = server or get_main_server()
    if callable(url):
        local_url = url.__name__
        server.add_route(local_url, url)
        return Route(url)

    def make_redirect(func: REDIRECT) -> Route[REDIRECT]:
        local_url = url
        if local_url is None:
            local_url = func.__name__
        server.add_route(local_url, func)
        return Route(func)

    return make_redirect
