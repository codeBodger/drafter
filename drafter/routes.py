from typing import Any, Concatenate, Generic, ParamSpec, Self, TypeVar, Union, Callable, Optional, TYPE_CHECKING, overload
from drafter.server import Server, get_main_server

if TYPE_CHECKING:
    from drafter.page import Page, Redirect

RETURN = TypeVar('RETURN', bound='Page', covariant=True)
PARAMS = ParamSpec('PARAMS')
STATE = TypeVar('STATE')

FUNC = TypeVar('FUNC', bound=Callable[Concatenate[Any, ...], 'Page'], covariant=True)

P = ParamSpec("P")
T = TypeVar("T")
S = TypeVar("S")
class UnCallable(Generic[FUNC]):

    def paramspec_from(self, _: Callable[P, T]) -> Callable[[Callable[Concatenate[Self, P], S]], Callable[Concatenate[Self, P], S]]:
        def _fnc(fnc: Callable[Concatenate[Self, P], S]) -> Callable[Concatenate[Self, P], S]:
            return fnc
        return _fnc

    def __init__(self, func: FUNC) -> None:
        self.func = func
        self.__call__ = self.paramspec_from(func)(self.__call__)
    
    def __getattr__(self, attr: str) -> Any:
        return getattr(self.func, attr)
    
    def __call__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("You shouldn't call routes and redirects!")



@overload
def route(url: Callable[[STATE], 'Page'], server: Optional[Server] = None) -> UnCallable[Callable[[STATE], 'Page']]: ...
@overload
def route(url: Optional[str] = None, server: Optional[Server] = None) -> Callable[
    [Callable[[STATE], 'Page']],
    UnCallable[Callable[[STATE], 'Page']]
]: ...

def route(url: Union[Callable[[STATE], 'Page'], str, None] = None, server: Optional[Server] = None) -> Union[
        UnCallable[Callable[[STATE], 'Page']],
        Callable[[Callable[[STATE], 'Page']], UnCallable[Callable[[STATE], 'Page']]]
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
        return UnCallable(url)

    def make_route(func: Callable[[STATE], 'Page']) -> UnCallable[Callable[[STATE], 'Page']]:
        local_url = url
        if local_url is None:
            local_url = func.__name__
        server.add_route(local_url, func)
        return UnCallable(func)

    return make_route


@overload
def redirect(url: Callable[Concatenate[STATE, PARAMS], 'Redirect'], server: Optional[Server] = None) -> UnCallable[Callable[Concatenate[STATE, PARAMS], 'Redirect']]: ...
@overload
def redirect(url: Optional[str] = None, server: Optional[Server] = None) -> Callable[
        [Callable[Concatenate[STATE, PARAMS], 'Redirect']],
        UnCallable[Callable[Concatenate[STATE, PARAMS], 'Redirect']]
    ]: ...

def redirect(url: Union[Callable[Concatenate[STATE, PARAMS], 'Redirect'], str, None] = None, server: Optional[Server] = None) -> Union[
        UnCallable[Callable[Concatenate[STATE, PARAMS], 'Redirect']],
        Callable[[Callable[Concatenate[STATE, PARAMS], 'Redirect']], UnCallable[Callable[Concatenate[STATE, PARAMS], 'Redirect']]]
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
        return UnCallable(url)

    def make_redirect(func: Callable[Concatenate[STATE, PARAMS], 'Redirect']) -> UnCallable[Callable[Concatenate[STATE, PARAMS], 'Redirect']]:
        local_url = url
        if local_url is None:
            local_url = func.__name__
        server.add_route(local_url, func)
        return UnCallable(func)

    return make_redirect
