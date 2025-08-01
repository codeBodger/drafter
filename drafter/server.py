import base64
import dataclasses
import html
from itertools import zip_longest
import os
import traceback
from dataclasses import dataclass, replace, field, fields
from functools import wraps
from typing import Any, Callable, Optional, List, Tuple, Union
import json
import inspect
import pathlib

from drafter.urls import friendly_urls
from drafter.components import PageContent
from drafter.configuration import ServerConfiguration
from drafter.constants import RESTORABLE_STATE_KEY, SUBMIT_BUTTON_KEY, PREVIOUSLY_PRESSED_BUTTON
from drafter.debug import DebugInformation
from drafter.history import VisitedPage, rehydrate_json, dehydrate_json, ConversionRecord, UnchangedRecord, \
    remap_hidden_form_parameters, safe_repr
from drafter.page import Page, _Page
from drafter.files import TEMPLATE_200, TEMPLATE_404, TEMPLATE_500, INCLUDE_STYLES, TEMPLATE_200_WITHOUT_HEADER, TEMPLATE_INDEX_HTML, \
    TEMPLATE_SKULPT_DEPLOY, seek_file_by_line
from drafter.raw_files import get_raw_files, get_themes
from drafter.urls import remove_url_query_params
from drafter.image_support import HAS_PILLOW, PILImage

import logging
logger = logging.getLogger('drafter')


DEFAULT_ALLOWED_EXTENSIONS = ('py', 'js', 'css', 'txt', 'json', 'csv', 'html', 'md')

def bundle_files_into_js(
        main_file: str, root_path: str,
        allowed_extensions: Optional[set[str]] = None,
        js_obj_name: Optional[str] = None,
        sep: Optional[str] = None,
        pref: Optional[str] = None
    ) -> tuple[str, list[str], list[str]]:
    """
    Bundles all files from a specified directory into a JavaScript-compatible format
    for Skulpt, a Python-to-JavaScript transpiler. The function traverses through the
    given directory, reads files with extensions present in the allowed extensions list,
    and aggregates them into a JavaScript code snippet. It also identifies files to be
    skipped and keeps a record of successfully added files.

    :param main_file: The path to the main Python file. This file will be labeled
        as "main.py" in the JavaScript output.
    :type main_file: str
    :param root_path: The root directory to search for files.
    :type root_path: str
    :param allowed_extensions: A collection of file extensions allowed for inclusion
        in the final JavaScript output. Defaults to a predefined tuple.
    :type allowed_extensions: set[str]
    :param js_obj_name: An optional alternative name for the JS object in which to store
        the python source.
    :type js_obj_name: str
    :param sep: The separator between each JSified file line.
    :type sep: str
    :param pref: An optional prefix for the filenames.  Defaults to empty string.
    :type pref: str
    :return: A tuple containing:
        - The combined JavaScript output string with file contents.
        - A list of skipped files that do not match the allowed extensions.
        - A list of added files that were successfully bundled.
    :rtype: tuple[str, list[str], list[str]]
    """
    allowed_extensions = allowed_extensions or set(DEFAULT_ALLOWED_EXTENSIONS)
    js_obj_name = js_obj_name or "Sk.builtinFiles.files"
    sep = sep or "\n"
    pref = pref or ""

    skipped_files: list[str] = []
    added_files: list[str] = []
    all_files = {}
    for root, dirs, files in os.walk(root_path):
        for file in files:
            is_main = os.path.join(root_path, file) == main_file
            path = pathlib.Path(os.path.join(root, file)).relative_to(root_path)
            if pathlib.Path(file).suffix[1:].lower() not in allowed_extensions:
                skipped_files.append(os.path.join(root, file))
                continue
            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                content = f.read()
                filename = str(path.as_posix()) if not is_main else "main.py"
                all_files[filename] = content
                added_files.append(os.path.join(root, file))

    js_lines = []
    for filename, contents in all_files.items():
        filename = pref + filename
        js_lines.append(f"{js_obj_name}[{filename!r}] = {contents!r};\n")

    return sep.join(js_lines), skipped_files, added_files


class Server:
    """
    Represents a server capable of managing routes, states, configurations, and error handling
    while supporting application setup and runtime logic.

    This class allows the definition of web routes, manages application states, handles errors
    gracefully, and provides a framework for deploying a web application with a structured
    configuration and support for image serving.

    :ivar routes: A dictionary mapping URLs to their respective handler functions.
    :type routes: dict
    :ivar _handle_route: Internal mapping for handler functions and their respective URLs.
    :type _handle_route: dict
    :ivar configuration: The configuration object representing server settings.
    :type configuration: ServerConfiguration
    :ivar _state: Current state of the application.
    :type _state: Any
    :ivar _initial_state: Serialized representation of the initial application state.
    :type _initial_state: str
    :ivar _initial_state_type: Type of the initial state.
    :type _initial_state_type: type
    :ivar _state_history: List tracking historical states of the application.
    :type _state_history: list
    # :ivar _state_frozen_history: List storing serialized snapshots of historical states.
    # :type _state_frozen_history: list
    :ivar _page_history: History of visited pages.
    :type _page_history: list
    :ivar _conversion_record: Internal record tracking parameter conversion processes.
    :type _conversion_record: list
    :ivar original_routes: List containing tuples of original route URLs and their handlers.
    :type original_routes: list
    :ivar _custom_name: Custom name for the server instance, used in string representations.
    :type _custom_name: str or None
    :ivar production: Whether the server is in production mode.
    :type production: bool
    :ivar image_folder: The folder to look for images in.
    :type image_folder: str
    """
    _page_history: List[Tuple[VisitedPage, str]]
    _custom_name = None

    def __init__(self, _custom_name: Union[str, None] = None, **kwargs: Any) -> None:
        self.routes: dict[str, Callable[..., str]] = {}
        self._handle_route: dict[Union[str, Callable[..., str]], Callable[..., str]] = {}
        self.configuration = ServerConfiguration(**kwargs)
        self._state: Any = None
        self._initial_state: Union[str, None] = None
        self._initial_state_type: Union[type, None] = None
        self._state_history: list[Any] = []
        # self._state_frozen_history = []
        self._page_history: List[Tuple[VisitedPage, str]] = []
        self._conversion_record: list[Union[ConversionRecord, UnchangedRecord]] = []
        self.original_routes: list[Tuple[str, Callable[..., Page]]] = []
        self._custom_name = _custom_name
        self.production = False
        self.image_folder = "images"

    def __repr__(self) -> str:
        """
        Provides a string representation of the current server object. If a custom
        name has been defined for the server instance, it returns that custom name.
        Otherwise, it provides a formatted string representation of the server's
        configuration.

        :return: The custom name of the server if defined, otherwise a string
            representation of the server's configuration.
        :rtype: str
        """
        if self._custom_name:
            return self._custom_name
        return f"Server({self.configuration!r})"

    def clear_routes(self) -> None:
        """
        Clears all stored routes from the `routes` attribute.

        This method removes all data within the `routes` attribute,
        resetting it back to its empty state. Use this when you want
        to remove all previous route configurations or stored paths
        within the object.
        """
        self.routes.clear()

    def dump_state(self) -> str:
        """
        Converts the current internal state of the State object into a JSON-encoded
        string. The internal state must be dehydratable using the provided
        utility function `dehydrate_json`.

        :raises TypeError: If any part of the internal state cannot be
            serialized into JSON due to invalid types.
        :raises ValueError: If serialization encounters unexpected value
            constraints or data inconsistencies.

        :return: A JSON string capturing the serialized format of the
            object's state.
        :rtype: str
        """
        return json.dumps(dehydrate_json(self._state))

    def load_from_state(self, state: str, state_type: type) -> Any:
        """
        Loads a specific State object from a serialized state based on the given state type.
        This method takes a serialized JSON string representation of a state and
        rehydrates it into the corresponding Python object according to the given state type.

        :param state: The serialized JSON string representation of the object state.
        :type state: str
        :param state_type: The class or data type to rehydrate the JSON state into.
        :type state_type: Type
        :return: The rehydrated Python object based on the state and state_type.
        :rtype: Any
        """
        return rehydrate_json(json.loads(state), state_type)

    def add_route(self, url: str, func: Callable[..., Page]) -> None:
        """
        Adds a route to the routing table for URL handling, ensuring the URL is unique
        and maps a function to the given route. Prepares the URL, processes the
        function into a valid callable, and stores the mapping for later resolution
        when the URL is accessed.

        :param url: The URL string to be added as a route. It must be unique.
        :type url: str
        :param func: The function to be associated with the provided URL. This
            function will be called when the route is accessed.
        :type func: Callable
        :raises ValueError: If the URL is already registered for another function.
        :return: None
        """
        if url in self.routes:
            raise ValueError(f"URL `{url}` already exists for an existing routed function: `{func.__name__}`")
        self.original_routes.append((url, func))
        url = friendly_urls(url)
        made_func = self.make_drafter_page(func)
        self.routes[url] = made_func
        self._handle_route[url] = self._handle_route[made_func] = made_func

    def reset(self) -> str:
        """
        Resets the current State object to its initial configuration and clears all
        recorded histories. After resetting, the function returns the result of the
        route mapped to '/' (the root index URL).

        :return: The result of the '/' route execution.
        :rtype: Page
        """
        if self._initial_state is None or self._initial_state_type is None:
            raise ValueError("You can't reset if you haven't setup!")
        self._state = self.load_from_state(self._initial_state, self._initial_state_type)
        self._state_history.clear()
        # self._state_frozen_history.clear()
        self._page_history.clear()
        self._conversion_record.clear()
        return self.routes['/'](self._state, [])

    def setup(self, initial_state: Any = None) -> None:
        """
        Initializes and configures the application. Sets up initial state, error
        pages, and application routes for handling requests.

        :param initial_state: The initial state to set up the application.
        :type initial_state: Any
        """
        self._state = initial_state
        self._initial_state = self.dump_state()
        self._initial_state_type = type(initial_state)

        # Setup routes
        if not self.routes:
            raise ValueError("No routes have been defined.\nDid you remember the @route decorator?")
        # TODO: Don't overwrite user-defined /--reset route
        self.routes["/--reset"] = lambda state, page_history: self.reset()
        if '/' not in self.routes:
            first_route = list(self.routes.values())[0]
            self.routes['/'] = first_route
        self.handle_images()

    def update_config(self, **kwargs: Any) -> None:
        """
        Executes the server application using the provided configuration. The method will
        update the configuration with any additional keyword arguments provided.

        :param kwargs: Arbitrary keyword arguments containing configuration updates. Only
            keys that match the ServerConfiguration fields will be applied.
        :return: None
        """
        # Update the configuration with the safe kwargs
        safe_keys = fields(ServerConfiguration)
        safe_key_names = {field.name for field in safe_keys}
        safe_kwargs: dict[str, Any] = {key: value for key, value in kwargs.items() if key in safe_key_names}
        updated_configuration = replace(self.configuration, **safe_kwargs)
        self.configuration = updated_configuration

    def prepare_args(self,
                     original_function: Callable[..., Any],
                     args: tuple[Any, ...], kwargs: dict[str, Any]
                    ) -> tuple[tuple[Any, ...], dict[str, Any], str, str]:
        """
        Processes and prepares arguments for the route function call, ensuring compatibility
        with expected parameters, handling state insertion, remapping parameters,
        and performing type conversion when necessary.

        :param original_function: The function whose parameters are being prepared.
        :param args: The positional arguments to be passed to the function.
        :param kwargs: The keyword arguments to be passed to the function.
        :return: A tuple containing:
            - Processed positional arguments matching the expected parameters of the
              function.
            - Processed keyword arguments matching the expected parameters of the
              function.
            - A string representation of the final arguments for logging or debugging.
            - The button pressed if detected and processed.
        """
        self._conversion_record.clear()
        # args: list[Any] = list(args)
        kwargs = dict(**kwargs)
        button_pressed = ""

        # params = get_params()
        # if SUBMIT_BUTTON_KEY in params:
        #     button_pressed = json.loads(params.pop(SUBMIT_BUTTON_KEY))
        # elif PREVIOUSLY_PRESSED_BUTTON in params:
        #     button_pressed = json.loads(params.pop(PREVIOUSLY_PRESSED_BUTTON))
        # TODO: Handle getting button_pressed;
            # remember to add it back to where it was removed in commit beb33ae
        # param_keys = list(params.keys())
        # for key in param_keys:
        #     kwargs[key] = params.pop(key)

        signature_parameters = inspect.signature(original_function).parameters
        # print(signature_parameters.get('kwargs', signature_parameters['state']).annotation)
        signature_parameters_no_state = list(signature_parameters.values())[1:]
        expected_parameters = list(signature_parameters.keys())[1:]
        show_names = {param.name: (param.kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.VAR_KEYWORD))
                      for param in signature_parameters.values()}
        # TODO: Clean up this mess; list comprehensions are nice, but we don't need to
            # loop through 5 times in lines this long
        var_pos_param = [*(param.name for param in signature_parameters_no_state if param.kind == inspect.Parameter.VAR_POSITIONAL), ""][0]
        var_kwd_param = [*(param.name for param in signature_parameters_no_state if param.kind == inspect.Parameter.VAR_KEYWORD), ""][0]
        expected_pos_params = [param.name for param in signature_parameters_no_state if param.kind == inspect.Parameter.POSITIONAL_ONLY]
        expected_pkw_params = [param.name for param in signature_parameters_no_state if param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD]
        expected_kwd_params = [param.name for param in signature_parameters_no_state if param.kind == inspect.Parameter.KEYWORD_ONLY]

        expected_pos_params = [*expected_pos_params, *expected_pkw_params]

        # TODO: Remove this, once we know it's never necessary
        # kwargs = remap_hidden_form_parameters(kwargs, button_pressed)
        # Insert state into the beginning of args
        # if (expected_parameters and expected_parameters[0] == "state") or (
        #         len(expected_parameters) - 1 == len(args) + len(kwargs)):
        #     args.insert(0, self._state)

        # Check if there are too many arguments
        if len(expected_pos_params) < len(args) and not var_pos_param:
            self.flash_warning(
                f"The {original_function.__name__} function expected at most {len(expected_pos_params)} "
                f"positional arguments, but {len(args)} were provided.\n"
                f"  Expected: {', '.join(expected_pos_params)}\n"
                f"  But got: {repr(args)}"
            )
            args = args[:len(expected_pos_params)]
        used_pos_params = expected_pos_params[:len(args)]
        for used_pos_param in used_pos_params:
            if expected_pkw_params[0] == used_pos_param:
                expected_pkw_params = expected_pkw_params[1:]
        expected_kwd_params = [*expected_pkw_params, *expected_kwd_params]
        if len(expected_kwd_params) < len(kwargs) and not var_kwd_param:
            self.flash_warning(
                f"The {original_function.__name__} function expected at most {len(expected_kwd_params)} "
                f"remaining positional arguments, but {len(kwargs)} were provided.\n"
                f"  Expected: {', '.join(expected_kwd_params)}\n"
                f"  But got: {repr(kwargs)}"
                + (
                    f"\n  After using {len(used_pos_params)} positional arguments:\n"
                    f"  For: {', '.join(used_pos_params)}\n"
                    f"  Having gotten: {repr(args)}"
                ) if len(used_pos_params) else ""
            )
            while len(expected_kwd_params) < len(kwargs):
                kwargs.pop(list(kwargs.keys())[-1])
        if len(expected_parameters) < len(args) + len(kwargs) and not var_pos_param and not var_kwd_param:
            raise ValueError("I really thought this was impossible!")
            self.flash_warning(
                f"The {original_function.__name__} function expected {len(expected_parameters)} parameters, but {len(args) + len(kwargs)} were provided.\n"
                f"  Expected: {', '.join(expected_parameters)}\n"
                f"  But got: {repr(args)} and {repr(kwargs)}"
            )
            # TODO: Select parameters to keep more intelligently by inspecting names
            args = args[:len(expected_parameters)]
            while len(expected_parameters) < len(args) + len(kwargs) and kwargs:
                kwargs.pop(list(kwargs.keys())[-1])
        # Type conversion if required
        expected_types = {name: p.annotation for name, p in
                          inspect.signature(original_function).parameters.items()}
        args = tuple(self.convert_parameter(param, val, expected_types, var_pos_param)
                for param, val in zip_longest(used_pos_params, args, fillvalue=""))
        kwargs = {param: self.convert_parameter(param, val, expected_types, var_kwd_param)
                  for param, val in kwargs.items()}
        # Verify all arguments are in expected_parameters
        for key, value in kwargs.items():
            if key not in expected_parameters and not var_kwd_param:
                raise ValueError(
                    f"Unexpected parameter {key}={value!r} in {original_function.__name__}. "
                    f"Expected parameters: {expected_parameters}. "
                )
        def index_or_len(item: tuple[str, Any]) -> int:
            try:
                return expected_parameters.index(item[0])
            except ValueError:
                return len(expected_parameters)
        # Final return result
        representation = [safe_repr(arg) for arg in args] + [
            f"{key}={safe_repr(value)}" if show_names.get(key, False) else safe_repr(value)
            for key, value in sorted(kwargs.items(), key=index_or_len)]
        return args, kwargs, ", ".join(representation), button_pressed

    def handle_images(self) -> None:
        """
        Handles the serving of images when the `deploy_image_path` is configured. This
        method maps a dynamic route to serve image files by their paths, allowing them
        to be accessed through the defined route.

        :raises AttributeError: If `self.configuration.deploy_image_path` or its
                                 attributes are not properly configured.
        :return: None
        """
        if self.configuration.deploy_image_path:
            # TODO: make this do anything
            # self.app.route(f"/{self.configuration.deploy_image_path}/<path:path>", 'GET', self.serve_image)
            # self.routes[f"/{self.configuration.deploy_image_path}/<path:path>"] = lambda state, path: self.serve_image(path)
            pass

    # TODO: make this do anything
    # def serve_image(self, path): # type: (str) -> bottle.HTTPResponse
    def serve_image(self, path): # type: (str) -> Any
        """
        Serves an image file located in the specified directory with the MIME type
        `image/png`. The method retrieves the image from the path provided, using
        the configured source image folder as the root directory.

        :param path: The relative path to the image file within the source image folder.
        :type path: str
        :return: The static file object representing the requested image.
        :rtype: static_file
        """
        raise NotImplementedError("serve_image is not yet implemented")
        # return static_file(path, root='./' + self.configuration.src_image_folder, mimetype='image/png')

    # TODO: Possibly use this for drafter.FileUpload?
    def try_special_conversions(self, value: Any, target_type: type) -> Any:
        """
        Attempts to convert the input value to the specified target type
            using the type's constructor.

        :param value: The input value to be converted.
        :type value: Any
        :param target_type: The desired type to convert the input value to. This can
            include types such as `bytes`, `str`, `dict`, or others, depending on the
            availability of appropriate conversion logic.
        :type target_type: type
        :return: The converted value as an instance of the specified target type.
            For now, the original value is passed to the target type for conversion.
        :rtype: Any
        """
        return target_type(value)

    def convert_parameter(self, param: str, val: Any, expected_types: dict[str, type], var_arg_name: str) -> Any:
        """
        Converts a given parameter value to a specified target type if possible, based
        on the expected types provided. Records successful conversions, unchanged
        parameters, and failed conversion attempts with detailed information.

        :param param: The name of the parameter to be converted.
        :type param: str
        :param val: The value of the parameter to be converted.
        :type val: Any
        :param expected_types: A dictionary containing the expected types for all
            parameters. The key is the parameter name, and the value is its expected
            type. If a parameter does not require conversion, its value is set to
            `inspect.Parameter.empty`.
        :type expected_types: dict
        :param var_arg_name: The name of the variable-length argument (either pos or kw).
            If `param` isn't in `expected_types`, it defaults to this value.
        :type var_arg_name: str
        :return: The converted value of the parameter if a conversion is successful;
            otherwise, the original value of the parameter.
        :rtype: Any
        :raises ValueError: If the value cannot be converted to its specified expected
            type, providing detailed information about the attempted conversion.
        """
        if param not in expected_types: param = var_arg_name

        if param in expected_types:
            expected_type = expected_types[param]
            if expected_type == inspect.Parameter.empty:
                self._conversion_record.append(UnchangedRecord(param, val, expected_types[param]))
                return val
            if hasattr(expected_type, '__origin__'):
                # TODO: Ignoring the element type for now, but should really handle that properly
                expected_type = expected_type.__origin__
            if not isinstance(val, expected_type):
                try:
                    target_type = expected_types[param]
                    converted_arg = self.try_special_conversions(val, target_type)
                    self._conversion_record.append(ConversionRecord(param, val, expected_types[param], converted_arg))
                except Exception as e:
                    try:
                        from_name = type(val).__name__
                        to_name = expected_types[param].__name__
                    except:
                        from_name = repr(type(val))
                        to_name = repr(expected_types[param])
                    raise ValueError(
                        f"Could not convert {param} ({val!r}) from {from_name} to {to_name}\n") from e
                return converted_arg
        # Fall through
        self._conversion_record.append(UnchangedRecord(param, val))
        return val

    def make_drafter_page(self, original_function: Callable[..., Page]) -> Callable[..., str]:
        """
        A decorator that wraps a given function to create a Drafter web environment.
        This includes processing parameters, building the page, verifying its content,
        and rendering it to the client. It also maintains state and history for the page
        creation and execution process.

        :param original_function: The original callable function to be wrapped
            and executed to construct the page.
        :return: A wrapped function that, when called, executes the original
            function with the added Drafter page handling logic.
        """
        @wraps(original_function)
        def drafter_page(state: Any, page_history: list[tuple[VisitedPage, str]], *args: Any, **kwargs: Any) -> str:
            # TODO: Handle SUBMIT_BUTTON_KEY, but (of course) not with that function.
                # RESTORABLE_STATE_KEY isn't needed, since we're always restoring that.
            # url = remove_url_query_params(request.url, {RESTORABLE_STATE_KEY, SUBMIT_BUTTON_KEY})

            try:
                args, kwargs, arguments, button_pressed = self.prepare_args(original_function, args, kwargs)
            except Exception as e:
                raise DrafterError("Error preparing arguments for page", e, original_function, self)
            # Actually start building up the page
            visiting_page = VisitedPage(original_function.__name__, original_function, arguments, "Creating Page", button_pressed)
            # self._page_history.append((visiting_page, original_state))
            self._page_history = [*page_history, (visiting_page, repr((json.dumps(dehydrate_json(state))))[1:-1].replace("\"", "\\\""))]
            try:
                page = original_function(state, *args, **kwargs)
            except Exception as e:
                additional_details = (f"  State: {state!r}\n"
                                      f"  Arguments: {args!r}\n"
                                      f"  Keyword Arguments: {kwargs!r}\n"
                                    #   f"  Button Pressed: {button_pressed!r}\n"
                                      f"  Function Signature: {inspect_signature_str(inspect.signature(original_function))}")
                raise DrafterError("Error creating page", e, original_function, self, additional_details)
            visiting_page.update("Verifying Page Result", original_page_content=page)
            self.verify_page_result(page, original_function)
            if False:
                pass # return verification_status
            try:
                page.verify_content(self)
            except Exception as e:
                raise DrafterError("Error verifying content", e, original_function, self)
            self._state_history.append(page.state)
            self._state = page.state
            visiting_page.update("Rendering Page Content")
            try:
                content = page.render_content(self.dump_state(), self.configuration)
            except Exception as e:
                raise DrafterError("Error rendering content", e, original_function, self)
            visiting_page.finish("Finished Page Load")
            if self.configuration.debug:
                content = content + self.make_debug_page()
            content = self.wrap_page(content)
            return content

        return drafter_page

    def stringify_history(self, history: Optional[list[tuple[VisitedPage, str]]]) -> str:
        history = history if history is not None else self._page_history
        return "\n|\n".join([f"{vp}\t|\t{s}" for vp, s in history])

    def destringify_history(self, hist_str: str) -> list[tuple[VisitedPage, str]]:
        if not hist_str: return []

        def make_entry(line: str) -> tuple[VisitedPage, str]:
            vp, s = line.split("\t|\t")
            return VisitedPage.fromstr(vp), s
        return [make_entry(line) for line in hist_str.split("\n|\n")]

    def verify_page_result(self, page: Page, original_function: Callable[..., Page]) -> None:
        """
        Verifies the result of a function execution to ensure it returns a valid `Page`
        object. The verification checks whether the returned result is of type `Page`
        and whether its structure adheres to the expected format. If the validation
        fails, an error message is generated and returned.

        :param page: The object returned by the endpoint method to be verified.
        :type page: Union[None, str, list, Any]
        :param original_function: A reference to the function or method where the
            `Page` object is expected to be returned from.
        :type original_function: Callable
        :return: Does not return any value as it raises an HTTP 500 error with the formatted message.
        :rtype: None
        """
        message = ""
        if page is None:
            message = (f"The server did not return a Page object from {original_function}.\n"
                       f"Instead, it returned None (which happens by default when you do not return anything else).\n"
                       f"Make sure you have a proper return statement for every branch!")
        elif isinstance(page, str):
            message = (
                f"The server did not return a Page() object from {original_function}. Instead, it returned a string:\n"
                f"  {page!r}\n"
                f"Make sure you are returning a Page object with the new state and a list of strings!")
        elif isinstance(page, list):
            message = (
                f"The server did not return a Page() object from {original_function}. Instead, it returned a list:\n"
                f" {page!r}\n"
                f"Make sure you return a Page object with the new state and the list of strings, not just the list of strings.")
        elif not isinstance(page, Page):
            message = (f"The server did not return a Page() object from {original_function}. Instead, it returned:\n"
                       f" {page!r}\n"
                       f"Make sure you return a Page object with the new state and the list of strings.")
        else:
            self.verify_page_state_history(page, original_function)
            if False:
                pass # return verification_status
            elif isinstance(page.content, str):
                message = (f"The server did not return a valid Page() object from {original_function}.\n"
                           f"Instead of a list of strings or content objects, the content field was a string:\n"
                           f" {page.content!r}\n"
                           f"Make sure you return a Page object with the new state and the list of strings/content objects.")
            elif not isinstance(page.content, list):
                message = (
                    f"The server did not return a valid Page() object from {original_function}.\n"
                    f"Instead of a list of strings or content objects, the content field was:\n"
                    f" {page.content!r}\n"
                    f"Make sure you return a Page object with the new state and the list of strings/content objects.")
            else:
                for item in page.content:
                    if not isinstance(item, (str, PageContent)):
                        message = (
                            f"The server did not return a valid Page() object from {original_function}.\n"
                            f"Instead of a list of strings or content objects, the content field was:\n"
                            f" {page.content!r}\n"
                            f"One of those items is not a string or a content object. Instead, it was:\n"
                            f" {item!r}\n"
                            f"Make sure you return a Page object with the new state and the list of strings/content objects.")

        if message:
            raise DrafterError("Error after creating page", ValueError(message), original_function, self)

    def verify_page_state_history(self, page: Page, original_function: Callable[..., Page]) -> None:
        """
        Validates the consistency of the state object's type in the provided `page`
        against the most recent state stored in the `self._state_history`. If any
        discrepancy is found in the type of the state object, it constructs an error
        message highlighting the inconsistency and generates an error page.

        :param page: The page object containing the state to be verified.
        :param original_function: The name of the function that created the page.
        :return: Returns an error page if a validation issue arises, otherwise none.
        """
        # TODO: rewrite as `verify_page_state_type` and verify against self._initial_state_type
            # Then, self._state_history is unneeded
        if not self._state_history:
            return
        message = ""
        last_type = self._state_history[-1].__class__
        if not isinstance(page.state, last_type):
            message = (
                f"The server did not return a valid Page() object from {original_function}. The state object's type changed from its previous type. The new value is:\n"
                f" {page.state!r}\n"
                f"The most recent value was:\n"
                f" {self._state_history[-1]!r}\n"
                f"The expected type was:\n"
                f" {last_type}\n"
                f"Make sure you return the same type each time.")
        # TODO: Typecheck each field
        if message:
            raise DrafterError("Error after creating page", ValueError(message), original_function, self)

    def wrap_page(self, content: str) -> str:
        """
        Wraps provided content in a styled HTML template, applying additional headers,
        scripts, styles, and any configuration-specific content.

        :param content: The content to be wrapped in the HTML template.
        :type content: str

        :raises ValueError: If the specified style in the configuration is not found
            in the list of included styles.

        :return: A fully formatted HTML string, including necessary headers, styles,
            scripts, and the provided content wrapped according to the configuration
            and selected style.
        :rtype: str
        """
        content = f"<div class='btlw'>{content}</div>"
        style = self.configuration.style
        global_files = get_raw_files("global") # type: ignore
        style_files = get_raw_files(style) # type: ignore
        if style_files is None:
            possible_themes = ", ".join(get_themes()) # type: ignore
            raise ValueError(f"Unknown style {style}. Please choose from {possible_themes}, or add a custom style tag with add_website_header.")

        scripts = "\n".join([*global_files.scripts.values(), *style_files.scripts.values()])
        styles = "\n".join([*global_files.styles.values(), *style_files.styles.values()])
        credit = "\n".join(c for c in [
            style_files.metadata.get('credit', ''),
            global_files.metadata.get('credit', ''),
        ] if c)
        if self.configuration.additional_header_content:
            header_content = "\n".join(self.configuration.additional_header_content)
        else:
            header_content = ""
        if self.configuration.additional_css_content:
            additional_css = "\n".join(self.configuration.additional_css_content)
            styles = f"{styles}\n<style>{additional_css}</style>"
        if self.configuration.skulpt:
            return TEMPLATE_200_WITHOUT_HEADER.format(
                header=header_content, styles=styles, scripts=scripts, content=content,
                title=json.dumps(self.configuration.title))
        else:
            return TEMPLATE_200.format(
                header=header_content, styles=styles, scripts=scripts, content=content,
                title=html.escape(self.configuration.title),
                credit=credit)

    def flash_warning(self, message: str) -> None:
        """
        This method displays a warning message. It is intended for immediate
        output to notify the user of a specific warning or issue.

        TODO: This should actually append to a list that gets shown in the debug area.

        :param message: The warning message to be displayed to the user.
        :type message: str
        :return: None
        """
        print(message)

    def make_debug_page(self) -> str:
        """
        Generates a debug page by gathering and processing various internal states and
        informational data.

        This method collects the page history, current state, routes, configuration,
        and conversion record to create a representation of a debug page. It utilizes
        these components to generate a structured output that represents the debug
        information.

        :return: Debug information page content generated based on the current internal
                 state and history of the application.
        :rtype: str
        """
        conv_rec = [rec for rec in self._conversion_record if isinstance(rec, ConversionRecord)]
        content = DebugInformation(self._page_history, self._state, self.routes, conv_rec,
                                   self.configuration)
        return content.generate()

    def bundled_js_or_error(
            self,
            allowed_extensions: Optional[set[str]] = None,
            js_obj_name: Optional[str] = None,
            sep: Optional[str] = None,
            pref: Optional[str] = None
        ) -> tuple[str, bool]:
        """
        Bundles files necessary for deployment, including the source code identified by
        the "start_server" line in the student's main file.

        This function searches for the entry point of the student's application and
        attempts to bundle it with all its associated files into a deployable format.
        If the main file cannot be located, it returns an error indicating the failure
        to find the required file. Otherwise, it creates a bundled JavaScript version
        of the required files.

        :param allowed_extensions: A collection of file extensions allowed for inclusion
            in the final JavaScript output. Passed directly on to `bundle_files_into_js`,
            where it defaults to a predefined tuple.
        :type allowed_extensions: set[str]
        :param js_obj_name: An alternative name for the JS object in which to store
            the python source. Passed directly on, where it defaults to `Sk.builtinFiles.files`.
        :type js_obj_name: str
        :param sep: The separator between each JSified file line, passed directly on,
            defaulting to newline.
        :type sep: str
        :param pref: An optional prefix for the filenames, passed directly on, defaults to ""
        :type pref: str
        :return: A tuple containing the bundled JS or error and an indication that an
            error occured (so you know what the first item is).
        :rtype: tuple[str, bool]
        """
        # Bundle up the necessary files, including the source code
        student_main_file = seek_file_by_line("start_server")
        if student_main_file is None:
            return TEMPLATE_500.format(title="500 Internal Server Error",
                                       message="Could not find the student's main file.",
                                       error="Could not find the student's main file.",
                                       routes=""), False
        bundled_js, skipped, added = bundle_files_into_js(
            student_main_file, os.path.dirname(student_main_file),
            allowed_extensions, js_obj_name, sep, pref
        )
        return bundled_js, True

    def test_deployment(self) -> str:
        """
        Bundles files and integrates them with the appropriate configurations
        for deployment, allowing the server to "deploy" a local version of the
        application, as it would appear on a live server using Skulpt.

        :raises IOError: If there are issues during the file bundling process.

        :return: HTML template string formatted with bundled JavaScript and CDN
                 configurations.
        :rtype: str
        """
        js_or_err, success = self.bundled_js_or_error()
        if not success: return js_or_err
        bundled_js = js_or_err
        return TEMPLATE_SKULPT_DEPLOY.format(website_code=bundled_js,
                                             cdn_skulpt=self.configuration.cdn_skulpt,
                                             cdn_skulpt_std=self.configuration.cdn_skulpt_std,
                                             cdn_skulpt_drafter=self.configuration.cdn_skulpt_drafter,
                                             cdn_drafter_setup=self.configuration.cdn_drafter_setup)

    def index_html_deployment(self) -> str:
        """
        Bundles files and integrates them with the appropriate configurations
        for local or remote deployment using skulpt.

        :return: HTML template string formatted with bundled source code and
            CDN configurations.
        :rtype: str
        """
        PYTHON_SOURCE_OBJECT_NAME = "pythonSource" if not self.configuration.debug else None
        pref = "src/student/" if self.configuration.debug else None
        js_or_err, success = self.bundled_js_or_error({"py"}, PYTHON_SOURCE_OBJECT_NAME, "            ", pref)
        if not success: return js_or_err
        else: bundled_js = js_or_err
        return TEMPLATE_INDEX_HTML.format(python_source_obj_name=PYTHON_SOURCE_OBJECT_NAME,
                                          python_source=bundled_js,
                                          cdn_skulpt=self.configuration.cdn_skulpt,
                                          cdn_skulpt_std=self.configuration.cdn_skulpt_std,
                                          cdn_skulpt_drafter=self.configuration.cdn_skulpt_drafter,
                                          cdn_drafter_setup=self.configuration.cdn_drafter_setup)


@dataclass
class DrafterError(BaseException):
    """
    Generates and displays a detailed error page upon encountering an issue in the application.

    This class formats a detailed error message by including the title of the error,
    the original function's name where the error occurred, the original error's message, and
    any additional details if provided. It also escapes potentially unsafe HTML characters
    from the error details and traceback to improve security. The formatted message is then
    displayed to the user.

    :param title: A brief, descriptive title for the error (e.g., "Server Error").
    :type title: str
    :param error: The original error/exception that was encountered.
    :type error: Exception
    :param original_function: The function object or name of the route being loaded
        where the error originated.
    :type original_function: Callable | str
    :param additional_details: Optional additional information or context about the error. Defaults to an empty string.
    :type additional_details: str
    """
    title: str
    error: Exception
    original_function: Union[Callable[..., Any], str]
    server: Server
    additional_details: str = ""

    def __post_init__(self) -> None:
        self.tb = html.escape(traceback.format_exc())
        self.title = f'<h1 style="display: inline-block;">{self.title}</h1>'

    def __str__(self) -> str:
        func_name = self.original_function.__name__ if callable(self.original_function) else self.original_function
        new_message = (
            f"<h1>Error in <code>{func_name}</code>:</h1>\n"
            f"<pre>{html.escape(str(self.error))}</pre>\n\n\n"
            f"<pre>{self.tb}</pre>"
        )
        if self.additional_details:
            new_message += ("\n\n\n<h1>Additional Details:</h1>\n"
                           f"<pre>{self.additional_details}</pre>")

        content = Page.frame_content(new_message, self.title)
        content += self.server.make_debug_page()
        content = self.server.wrap_page(content)
        return content


MAIN_SERVER = Server(_custom_name="MAIN_SERVER")

def set_main_server(server: Server) -> None:
    """
    Sets the main server to the given server. This is useful for testing purposes.

    :param server: The server to set as the main server
    :return: None
    """
    global MAIN_SERVER
    MAIN_SERVER = server

def get_main_server() -> Server:
    """
    Gets the main server. This is useful for testing purposes.

    :return: The main server
    """
    return MAIN_SERVER

def get_all_routes(server: Optional[Server] = None) -> dict[str, Callable[..., str]]:
    """
    Get all routes available in the given server or the main server if none is provided.

    If the `server` parameter is not specified, the function retrieves the main server
    using the `get_main_server()` function and returns its list of routes.

    :param server: An optional `Server` instance. If provided, the method will retrieve
        routes specific to this server. If omitted, it defaults to the main server.
    :type server: Optional[Server]

    :return: Returns a list of routes from the provided or default main server.
    """
    if server is None:
        server = get_main_server()
    return server.routes

def get_server_setting(key: str, default: Optional[Any] = None, server: Server = MAIN_SERVER) -> Any:
    """
    Gets a setting from the server's configuration. If the setting is not found, the default value is returned.

    :param key: The key to look up in the configuration
    :param default: The default value to return if the key is not found
    :param server: The server to look up the setting in (defaults to the ``MAIN_SERVER``)
    :return: The value of the setting, or the default value if not found
    """
    return getattr(server.configuration, key, default)

def inspect_signature_str(sig: inspect.Signature) -> str:
    """Create a string representation of the Signature object.

    If *max_width* integer is passed,
    signature will try to fit into the *max_width*.
    If signature is longer than *max_width*,
    all parameters will be on separate lines.

    If *quote_annotation_strings* is False, annotations
    in the signature are displayed without opening and closing quotation
    marks. This is useful when the signature was created with the
    STRING format or when ``from __future__ import annotations`` was used.
    """
    result: list[str] = []
    render_pos_only_separator = False
    render_kw_only_separator = True
    for param in sig.parameters.values():
        formatted = inspect_parameter_str(param)

        kind = param.kind

        if kind == inspect.Parameter.POSITIONAL_ONLY:
            render_pos_only_separator = True
        elif render_pos_only_separator:
            # It's not a positional-only parameter, and the flag
            # is set to 'True' (there were pos-only params before.)
            result.append('/')
            render_pos_only_separator = False

        if kind == inspect.Parameter.VAR_POSITIONAL:
            # OK, we have an '*args'-like parameter, so we won't need
            # a '*' to separate keyword-only arguments
            render_kw_only_separator = False
        elif kind == inspect.Parameter.KEYWORD_ONLY and render_kw_only_separator:
            # We have a keyword-only parameter to render and we haven't
            # rendered an '*args'-like parameter before, so add a '*'
            # separator to the parameters list ("foo(arg1, *, arg2)" case)
            result.append('*')
            # This condition should be only triggered once, so
            # reset the flag
            render_kw_only_separator = False

        result.append(formatted)

    if render_pos_only_separator:
        # There were only positional-only parameters, hence the
        # flag was not reset to 'False'
        result.append('/')

    rendered = f"({', '.join(result)})"

    return rendered

def inspect_parameter_str(param: inspect.Parameter) -> str:
    kind = param.kind
    formatted = param.name

    # Add annotation and default value
    if repr(param.annotation) != "<dataclasses.EmptyParameter object>":
        annotation = inspect_formatannotation(param.annotation)
        formatted = f"{formatted}: {annotation}"

    if repr(param.default) != "<dataclasses.EmptyParameter object>":
        if repr(param.annotation) != "<dataclasses.EmptyParameter object>":
            formatted = f"{formatted} = {param.default!r}"
        else:
            formatted = f"{formatted}={param.default!r}"

    if kind == inspect.Parameter.VAR_POSITIONAL:
        formatted = '*' + formatted
    elif kind == inspect.Parameter.VAR_KEYWORD:
        formatted = '**' + formatted

    return formatted

def inspect_formatannotation(annotation: Any) -> str:
    import types
    if getattr(annotation, '__module__', None) == 'typing':
        return repr(annotation).strip(" \t\n.").replace("typing.", "")
    if isinstance(annotation, types.GenericAlias):
        return str(annotation)
    if isinstance(annotation, type):
        if annotation.__module__ in ('builtins', None):
            return annotation.__qualname__
        return annotation.__module__+'.'+annotation.__qualname__
    return repr(annotation)

def render_route(route: str, state_str: str, page_history_str: str, args: str, kwargs: str, inputs: str) -> tuple[str, str, str]:
    """
    Renders the route specified with the state and arguments specified.
    Returns the site content and new state.

    :param route: The name of the route to render.
    :type route: str
    :param state_str: The current state of the website, probably from localStorage.
    :type state_str: str
    :param page_history_str: The history of the website, probably from localStorage.
    :type page_history_str: str
    :param args: The JSONified positional arguments.
    :type args: str
    :param kwargs: The JSONified keyword arguments.
    :type kwargs: str
    :param inputs: All of the <input> tags, JSONified. 
    :type inputs: str
    :return: the text content of the site, state, and history.
    :rtype: tuple[str, str, str]
    """
    server = get_main_server()
    if server._initial_state_type is None:
        raise ValueError("You can't render a route if you haven't setup!")
    state = server.load_from_state(state_str, server._initial_state_type)
    server._state = state
    page_history = server.destringify_history(page_history_str)
    server._page_history = page_history
    py_args = json.loads(base64.b64decode(bytes(args, 'utf-8')).decode('utf-8'))
    py_kwargs = json.loads(base64.b64decode(bytes(kwargs, 'utf-8')).decode('utf-8'))
    py_kwargs.update(json.loads(base64.b64decode(bytes(inputs, 'utf-8')).decode('utf-8')))

    try:
        page = server.routes[route](state, page_history, *py_args, **py_kwargs)
    except DrafterError as e:
        return str(e), state_str, server.stringify_history(server._page_history)
    except Exception as e:
        # tb = html.escape("<br>".join(traceback.format_exc().split("\n")))
        # tb = html.escape(traceback.format_exc())
        err = DrafterError("Unknown Error", e, route, server)
        return str(err), state_str, server.stringify_history(server._page_history)

    # print(1009, server._page_history[0][0])
    # print(1010, json.dumps(server._page_history[0][0]))

    return page, server.dump_state(), server.stringify_history(server._page_history)


def start_server(initial_state: Any = None, server: Server = MAIN_SERVER, skip: bool = False, **kwargs: Any) -> None:
    """
    Starts the server with the given initial state and configuration.
    If not running in Skulpt, starts a local HTTP server from which it runs everything.
    If the server is set to skip, it will not start.
    Additional keyword arguments will be passed to the server's update_config method.
    This can be used to control things like the ``port``.

    :param initial_state: The initial state to start the server with
    :param server: The server to run on, defaulting to ``MAIN_SERVER``
    :param skip: If True, the server will not start; this is useful for running tests headlessly
    :param kwargs: Additional keyword arguments to pass to the server's run method. See below.
    :return: None

    :Keyword Arguments:
        * *port* (``int``) --
          The port to run the server on. Defaults to ``8080``
    """
    kwargs["port"] = kwargs.get("port", 8080)
    if server.configuration.skip or skip:
        logger.info("Skipping server setup and execution")
        return

    if server.configuration.skulpt:
        server.setup(initial_state)
        server.update_config(**kwargs) # TODO: always do this
        # global SITE
        # SITE = str(server.routes["/"](server._state))
        # SITE = str(server.routes["/"]())
    else:
        if server.configuration.debug:
            server.configuration.cdn_skulpt_drafter = server.configuration.cdn_skulpt_drafter.replace(".js", "-py.js")

        with open("index.html", "w") as f:
            f.write(server.index_html_deployment())

        # TODO: We shouldn't need any server in the end; this is just useful for dev
        from http.server import test, SimpleHTTPRequestHandler, ThreadingHTTPServer # type: ignore[attr-defined]
        test(SimpleHTTPRequestHandler, ThreadingHTTPServer, **kwargs)
