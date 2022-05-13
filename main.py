import enum
import logging
from typing import Callable, List

from itertools import count

from anytree import Node, RenderTree, NodeMixin

from conf import config as cfg
import redis

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
            raise ValueError(f'self.items should contain items of type str, dict, list or tuple, not {self.items[0].__class__.__name__}')

    def get_item(self, idx):
        _items = [i for i in enumerate(self.items, start=1)]
        return _items[idx]


class FormFlow(BaseUSSD):
    def __init__(self, current_step, last_input, form_questions: dict, channel: Channels, step_validator: Callable,
                 msisdn, session_id):

        super().__init__(msisdn, session_id, channel)
        self.invalid_input = "CON Invalid input\n{menu}"
        self.current_step = int(float(current_step))
        self.previous_step = self.current_step if self.current_step >= 1 else None
        self.last_input = last_input
        self.form_questions = form_questions
        self.step_validator = step_validator

    def _validate_last_input(self):
        """
        validate by using if...else, for all steps in this flow
        :return:
        """

        _val, _validated = self.step_validator(self.current_step, self.last_input)
        if _val is None or not isinstance(_val, bool):
            logger.warning('Input not validated explicitly by validator function, Default value of True has been used')
            _val = True
        if _validated is not None:
            self.last_input = _validated

        return _val

    def _response(self):
        skip_validation = False
        valid_last_input = False

        # we skip validation, since we are going back, we just display the menu
        if self.last_input == self.back_symbol:
            valid_last_input = True
            skip_validation = True
            self.current_step -= 2

        if not skip_validation:
            # validate last input.
            valid_last_input = self._validate_last_input()

        # if last input is valid, display next menu, otherwise, show invalid input message, and display same menu
        if valid_last_input:
            try:
                resp = self.form_questions[str(self.current_step + 1)]

            except ValueError:
                resp = None

            except KeyError:
                if self.current_step == -1:
                    msg = "CON We'll be going back to the previous menu..."
                    logger.warning(msg[4:])
                    resp = msg
                elif self.current_step == len(self.form_questions):
                    msg = 'END Next step not specified'
                    logger.warning(msg[4:])
                    resp = msg
                else:
                    msg = 'END Step response not specified'
                    logger.warning(msg[4:])
                    resp = msg

        else:
            resp = self.invalid_input.format(menu=self.form_questions[str(self.current_step)]['menu'][4:])

        if isinstance(resp, ListInput):
            resp = resp.get_items()

        if isinstance(resp, dict) and 'menu' in resp.keys():
            if isinstance(resp['menu'], ListInput):
                resp['menu'] = resp['menu'].get_items()
        return resp

    def get_response(self):
        _resp = self._response()
        if isinstance(_resp, dict) and 'menu' in _resp.keys():
            message = _resp['menu']
        else:
            message = _resp

        if self.channel in [Channels.WHATSAPP, Channels.TELEGRAM]:
            message = message[4:]

        return message


class UssdMenu(Node, NodeMixin):
    _ids = count(0)

    def __init__(self, name="", title: str = "", show_title: bool = True, **kwargs):
        super().__init__(name, **kwargs)
        self.title = title
        self.show_title = show_title
        self.id = next(self._ids)
        self._generate_id()

        self.label = self.id,
        self.menu_string = ""
        self.menu_string += "" if self.parent is None else "\n0: BACK\n00: MAIN MENU"
        if not kwargs.get('terminal'):
            _ = self.parent.generate_menu() if self.parent is not None else ''
        else:
            self.menu_string = self.title

        self.all_ids = next(self._ids)  # TODO: remove this after testing..

        parents = kwargs.get('parents')
        if parents:
            if isinstance(parents, list):
                for parent in parents:
                    parent.children.append(self)

    def generate_menu(self):
        self.menu_string = "\n".join([f"{i.id}. {i.title}" for i in self.children]) if self.children else ""
        return ''  # not used.

    def _generate_id(self):
        if self.parent is not None:
            _children = self.parent.children
            self.id = len(_children)

    def __repr__(self):
        classname = self.__class__.__name__
        return "%s(%s %s)" % (classname, self.parent.title if self.parent is not None else "", " -> " + self.title)

