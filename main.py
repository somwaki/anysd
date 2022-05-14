import enum
import logging
from itertools import count
from typing import Callable, List

import redis
from anytree import Node, NodeMixin

from conf import config as cfg, FormBackError

LOG_FORMAT = '%(asctime)s %(levelname)-6s %(funcName)s (on line %(lineno)-4d) : %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


class Channels(enum.Enum):
    USSD = 'ussd'
    WHATSAPP = 'whatsapp'
    TELEGRAM = 'telegram'


class BaseUSSD:
    def __init__(self, msisdn, session_id, channel: Channels):
        self.msisdn = msisdn
        self.session_id = session_id
        self.redis_key = f"{self.msisdn}:{self.session_id}"
        self.channel = Channels(channel)
        self.r = redis.Redis(**cfg['redis'])

        nav: dict = cfg.get('navigation')
        if nav:
            self.back_symbol = str(nav.get('back_symbol'))
        else:
            self.back_symbol = '0'


class ListInput:

    def __init__(self, items: List, title: str, key=None, idx=None):
        """
        For handling Listable items

        :param items: list of items to be displayed
        :param title: The title to add on the menu
        :param key: [Optional] if the list is a list of `dict` objects, the `value` of the `key` is used from each dict
                    to create the menu
        :param idx: [Optional] if the list is a list of `tuple` or `list` then the idx is the index of element in each `tuple` or `list`
                    used to create the menu
        """
        self.items = items
        self.title = title
        self.key = key
        self.idx = idx

    def get_items(self):
        if not isinstance(self.items, list):
            raise ValueError(f'self.items should be of type list, not {self.items.__class__.__name__}')
        if not self.items:
            raise ValueError(f'The list appears to be empty. ')

        if isinstance(self.items[0], (str, int, float)):
            r = '\n'.join([f'{idx}. {str(item)}' for idx, item in enumerate(self.items, start=1)])
            return f'CON {self.title}\n {r}'

        elif isinstance(self.items[0], dict):
            r = '\n'.join([f'{idx}. {item[self.key]}' for idx, item in enumerate(self.items, start=1)])
            return f'CON {self.title}\n {r}'

        elif isinstance(self.items[0], list) or isinstance(self.items[0], tuple):
            r = '\n'.join([f'{idx}. {item[idx]}' for idx, item in enumerate(self.items, start=1)])
            return f'CON {self.title}\n {r}'

        else:
            raise ValueError(
                f'self.items should contain items of type str, dict, list or tuple, not {self.items[0].__class__.__name__}')

    def get_item(self, idx):
        _items = [i for i in enumerate(self.items, start=1)]
        return _items[idx]


class FormFlow(BaseUSSD):
    def __init__(self, form_questions: dict, channel: Channels, step_validator: Callable,
                 msisdn, session_id):

        super().__init__(msisdn, session_id, channel)
        self.invalid_input = "CON Invalid input\n{menu}"
        self.form_questions = form_questions
        self.step_validator = step_validator

    def _validate_last_input(self, current_step, last_input):
        """
        validate by using if...else, for all steps in this flow
        :return:
        """

        _val, _validated = self.step_validator(current_step, last_input)
        if _val is None or not isinstance(_val, bool):
            logger.warning('Input not validated explicitly by validator function, Default value of True has been used')
            _val = True
        if _validated is not None:
            last_input = _validated

        return _val

    def _response(self, current_step, last_input):
        skip_validation = False
        valid_last_input = False

        # we skip validation, since we are going back, we just display the menu
        if last_input == self.back_symbol:
            valid_last_input = True
            skip_validation = True
            current_step -= 2

        if not skip_validation:
            # validate last input.
            valid_last_input = self._validate_last_input(current_step, last_input)

        # if last input is valid, display next menu, otherwise, show invalid input message, and display same menu
        if valid_last_input:
            try:
                resp = self.form_questions[str(current_step + 1)]

            except ValueError:
                resp = None

            except KeyError:
                if current_step == -1:
                    raise FormBackError('Cannot go back beyond this point')
                elif current_step == len(self.form_questions):
                    msg = 'END Next step not specified'
                    logger.warning(msg[4:])
                    resp = msg
                else:
                    msg = 'END Step response not specified'
                    logger.warning(msg[4:])
                    resp = msg

        else:
            resp = self.invalid_input.format(menu=self.form_questions[str(current_step)]['menu'][4:])

        if isinstance(resp, ListInput):
            resp = resp.get_items()

        if isinstance(resp, dict) and 'menu' in resp.keys():
            if isinstance(resp['menu'], ListInput):
                resp['menu'] = resp['menu'].get_items()
        return resp

    def get_response(self, current_step, last_input):
        if current_step is None:
            current_step = 0
        _resp = self._response(current_step, last_input)
        if isinstance(_resp, dict) and 'menu' in _resp.keys():
            message = _resp['menu']
        else:
            message = _resp

        if self.channel in [Channels.WHATSAPP, Channels.TELEGRAM]:
            message = message[4:]

        return message


class NavigationMenu(Node, NodeMixin):
    _ids = count(0)

    def __init__(self, name="", title: str = "", show_title: bool = True, next_form=None, **kwargs):
        super().__init__(name, **kwargs)
        self.next_form = next_form
        self.title = title
        self.show_title = show_title
        self.id = next(self._ids)
        self._generate_id()

        self.label = self.id,
        self.menu_string = ""
        self.menu_string += "" if self.parent is None else "\n0: BACK\n00: MAIN MENU"

        self.all_ids = next(self._ids)  # TODO: remove this after testing..

    def _generate_menu(self, last_input, step=None):
        if len(self.children) == 0 and self.next_form is not None:
            if not isinstance(self.next_form, FormFlow):
                raise ValueError(
                    f"'next_form' should be of type {type(FormFlow)} not {self.next_form.__class__.__name__}")

            self.menu_string = getattr(self.next_form, 'get_response')(step, last_input)

        elif len(self.children) == 0:
            raise ValueError("Either children or next_form should be set to define next action")
        else:
            self.menu_string = "\n".join([f"{i.id}. {i.title}" for i in self.children]) if self.children else ""

        if self.show_title:
            self.menu_string = f'{self.title}\n{self.menu_string}'

    def get_menu(self, last_input, step=None):
        self._generate_menu(last_input, step)
        return self.menu_string

    def _generate_id(self):
        if self.parent is not None:
            _children = self.parent.children
            self.id = len(_children)


def path_processor(path_as_list: list, index=1, back_symbol: str = '0', home_symbol: str = '00'):
    path = path_as_list

    if path and path[0] in [str(back_symbol), str(home_symbol)]:
        # invalid, we can't start by going back
        raise ValueError("The path does not seem to be valid")

    if len(path) == 1:
        # only one path and is not 0, just return it
        return path

    if index + 1 > len(path):
        # reached the end of list, nothing beyond to compare
        return path

    if path[index] == str(back_symbol):
        path.pop(index)
        path.pop(index - 1)

        if len(path) > index - 1:
            return path_processor(path, index - 1)

    elif path[index] == str(home_symbol):
        for i in range(index + 1):
            path.pop(0)

        if len(path) > 1:
            return path_processor(path, index=1)
    else:
        return path_processor(path, index + 1)

    return path


def path_navigator(start: NavigationMenu, path: list):
    if len(path) == 0:
        return start

    idx = path.pop(0)
    child = start.children[idx - 1]

    return path_navigator(child, path)


def navigate(home_menu: NavigationMenu, step, ussd_string, processed_path):
    def _menu(path):
        _menu_ref: NavigationMenu = path_navigator(home_menu, path.copy())
        _resp = getattr(_menu_ref, 'get_menu')(last_input=ussd_string.split("*")[-1], step=step)
        return _resp
    try:
        resp = _menu(processed_path)
    except FormBackError:
        processed_path.pop()
        resp = _menu(processed_path)

    return resp
