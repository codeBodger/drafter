from typing import Union, Callable, Optional, TYPE_CHECKING, overload
from drafter.server import Server, get_main_server

if TYPE_CHECKING:
    from drafter.page import Page


@overload
def route(url: Callable[..., 'Page'], server: Optional[Server] = None) -> Callable[..., 'Page']: ...
@overload
def route(url: Optional[str] = None, server: Optional[Server] = None) -> Callable[..., Callable[..., 'Page']]: ...

def route(url: Union[Callable[..., 'Page'], str, None] = None, server: Optional[Server] = None) -> Callable[..., Union['Page', Callable[..., 'Page']]]:
    """
    Main function to add a new route to the server. Recommended to use as a decorator.
    Once added, the route will be available at the given URL; the function name will be used if no URL is provided.
    When you go to the URL, the function will be called and its return value will be displayed.

    :param url: The URL to add the route to. If None, the function name will be used.
    :param server: The server to add the route to. Defaults to the main server.
    :return: The modified route function.
    """
    server = server or get_main_server()
    if callable(url):
        local_url = url.__name__
        server.add_route(local_url, url)
        return url

    def make_route(func: Callable[..., 'Page']) -> Callable[..., 'Page']:
        local_url = url
        if local_url is None:
            local_url = func.__name__
        server.add_route(local_url, func)
        return func

    return make_route
