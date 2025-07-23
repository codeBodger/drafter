from typing import Any, Union, Callable, Optional, TYPE_CHECKING, overload
from drafter.server import Server, get_main_server

if TYPE_CHECKING:
    from drafter.page import Page, Redirect


@overload
def route(url: Callable[[Any], 'Page'], server: Optional[Server] = None) -> Callable[[Any], 'Page']: ...
@overload
def route(url: Optional[str] = None, server: Optional[Server] = None) -> Callable[[Callable[[Any], 'Page']], Callable[[Any], 'Page']]: ...

def route(url: Union[Callable[[Any], 'Page'], str, None] = None, server: Optional[Server] = None) -> Union[
        Callable[[Any], 'Page'],
        Callable[[Callable[[Any], 'Page']], Callable[[Any], 'Page']]
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
        return url

    def make_route(func: Callable[[Any], 'Page']) -> Callable[[Any], 'Page']:
        local_url = url
        if local_url is None:
            local_url = func.__name__
        server.add_route(local_url, func)
        return func

    return make_route


@overload
def redirect(url: Callable[..., 'Redirect'], server: Optional[Server] = None) -> Callable[..., 'Redirect']: ...
@overload
def redirect(url: Optional[str] = None, server: Optional[Server] = None) -> Callable[
        [Callable[..., 'Redirect']],
        Callable[..., 'Redirect']
    ]: ...

def redirect(url: Union[Callable[..., 'Redirect'], str, None] = None, server: Optional[Server] = None) -> Union[
        Callable[..., 'Redirect'],
        Callable[[Callable[..., 'Redirect']], Callable[..., 'Redirect']]
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
        return url

    def make_redirect(func: Callable[..., 'Redirect']) -> Callable[..., 'Redirect']:
        local_url = url
        if local_url is None:
            local_url = func.__name__
        server.add_route(local_url, func)
        return func

    return make_redirect
