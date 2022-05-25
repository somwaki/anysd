import enum
import json
import logging
from itertools import count
from typing import Callable, List

import redis
from anytree import Node, NodeMixin

from .conf import config as cfg, FormBackError, r, back_symbol, home_symbol, NavigationBackError, NavigationInvalidChoice

LOG_FORMAT = '%(asctime)s %(levelname)-6s %(funcName)s (on line %(lineno)-4d) : %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


class Channels(enum.Enum):
    USSD = 'ussd'
    WHATSAPP = 'whatsapp'
    TELEGRAM = 'telegram'


class BaseUSSD:
    def __init__(self, msisdn, session_id, ussd_string):
        self.msisdn = msisdn
        self.session_id = session_id
        self.redis_key = f"{self.msisdn}:{self.session_id}"
        self.r = redis.Redis(**cfg['redis'])
        self.ussd_string = ussd_string


class ShortCutHandler:
    pass


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
            rsp = '\n'.join([f'{idx}. {str(item)}' for idx, item in enumerate(self.items, start=1)])
            return f'CON {self.title}\n{rsp}'

        elif isinstance(self.items[0], dict):
            rsp = '\n'.join([f'{idx}. {item[self.key]}' for idx, item in enumerate(self.items, start=1)])
            return f'CON {self.title}\n{rsp}'

        elif isinstance(self.items[0], list) or isinstance(self.items[0], tuple):
            rsp = '\n'.join([f'{idx}. {item[idx]}' for idx, item in enumerate(self.items, start=1)])
            return f'CON {self.title}\n{rsp}'

        else:
            raise ValueError(
                f'self.items should contain items of type str, dict, list or tuple, not {self.items[0].__class__.__name__}')

    def get_item(self, idx):
        _items = [i for i in enumerate(self.items, start=1)]
        return _items[idx]

    def validate(self, key):
        try:
            key = int(key)
            if key in range(1, len(self.items)):
                return True
            return False
        except ValueError:
            return False


class FormFlow:
    # class FormFlow(BaseUSSD):
    # def __init__(self, form_questions: dict, step_validator: Callable, msisdn, session_id, ussd_string):
    def __init__(self, form_questions: dict, step_validator: Callable):

        # super().__init__(msisdn, session_id, ussd_string)
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

        return _val

    def _response(self, current_step, last_input):
        skip_validation = False
        valid_last_input = False

        _state = {}

        # we skip validation, since we are going back, we just display the menu
        if last_input == back_symbol:
            valid_last_input = True
            skip_validation = True
            current_step -= 2
            _state['FORM_STEP'] = current_step

        if not skip_validation:
            # validate last input.
            valid_last_input = self._validate_last_input(current_step, last_input)

        # if last input is valid, display next menu, otherwise, show invalid input message, and display same menu
        if valid_last_input:
            try:
                resp = self.form_questions[str(current_step + 1)]

                # increment step here
                _state['FORM_STEP'] = current_step + 1
            except ValueError:
                resp = None

            except KeyError or IndexError:
                if current_step <= -1:
                    # r.hdel(self.redis_key, *['FORM_STEP'])
                    _state['FORM_STEP'] = None
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
            if isinstance(self.form_questions[str(current_step)]['menu'], ListInput):
                resp = self.invalid_input.format(menu=self.form_questions[str(current_step)]['menu'].get_items()[4:])
            else:
                resp = self.invalid_input.format(menu=self.form_questions[str(current_step)]['menu'][4:])

        if isinstance(resp, ListInput):
            resp = resp.get_items()

        elif callable(resp):
            resp = resp()

        if isinstance(resp, dict) and 'menu' in resp.keys():
            if isinstance(resp['menu'], ListInput):
                resp['menu'] = resp['menu'].get_items()
        return resp, _state, valid_last_input

    def get_response(self, current_step, last_input):
        if current_step is None:
            current_step = 0
        _resp, state, valid = self._response(current_step, last_input)
        if isinstance(_resp, dict) and 'menu' in _resp.keys():
            message = _resp['menu']
        else:
            message = _resp

        return message, state, valid


class NavigationMenu(Node, NodeMixin):
    _ids = count(0)

    def __init__(self, name="", title: str = "", show_title: bool = True, next_form=None, **kwargs):
        super().__init__(name, **kwargs)
        self.next_form = next_form
        self.title = title
        self.show_title = show_title
        self.id = next(self._ids)
        self._generate_id()
        self.form_state = None
        self.valid_last_input = None
        self.label = self.id,
        self.menu_string = ""
        self.menu_string += "" if self.parent is None else "\n0: BACK\n00: MAIN MENU"

        self.all_ids = next(self._ids)  # TODO: remove this after testing..

    def _generate_menu(self, last_input, step=None):
        if len(self.children) == 0 and self.next_form is not None:
            # form variable is set but it is not a FormFlow class
            if not isinstance(self.next_form, FormFlow):
                raise ValueError(
                    f"'next_form' should be of type {type(FormFlow)} not {self.next_form.__class__.__name__}")

            # Here means this Node has no children but has next_form set

            _message, _state, valid = getattr(self.next_form, 'get_response')(step, last_input)
            self.menu_string = _message
            self.form_state = _state
            self.valid_last_input = valid

        # Node has no children and no form to call
        elif len(self.children) == 0:
            raise ValueError("Either children or next_form should be set to define next action")

        else:
            if last_input == back_symbol:
                raise NavigationBackError('We are at home')

            # Navigating through nodes. Here it means we are at a node which has children. so we will display the
            # children as menu
            self.menu_string = f"CON Select {self.title}:\n" + "\n".join([f"{i.id}. {i.title}" for i in self.children]) if self.children else ""

        # if self.show_title:
        #     self.menu_string = f'{self.title}\n{self.menu_string[4:] if self.menu_string[0:2] in ["CON", "END"] else self.menu_string}'

    def get_menu(self, last_input, step=None):
        self._generate_menu(last_input, step)
        return self.menu_string, self.form_state, self.valid_last_input

    def _generate_id(self):
        if self.parent is not None:
            _children = self.parent.children
            self.id = len(_children)


class NavigationController(BaseUSSD):
    def __init__(self, home_menu: NavigationMenu, msisdn, session_id, ussd_string, channel: Channels = Channels.USSD):

        super().__init__(msisdn, session_id, ussd_string)
        self.home_menu = home_menu

    def path_processor(self, path_as_list: list = None, index=1, ):
        path = path_as_list
        if path is None:
            path = json.loads(r.hget(self.redis_key, 'PATH_AS_LIST'))

        if path and path[0] in [str(back_symbol), str(home_symbol)]:
            # invalid, we can't start by going back
            return []

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
                return self.path_processor(path, index - 1)

        elif path[index] == str(home_symbol):
            for i in range(index + 1):
                path.pop(0)

            if len(path) > 1:
                return self.path_processor(path, index=1)
        else:
            return self.path_processor(path, index + 1)

        return path

    def path_to_list(self, start: NavigationMenu, path=None):

        if path is None:
            path = []
        if start.parent is None:
            return path
        else:
            path.insert(0, start.parent.children.index(start) + 1)
            start = start.parent
            return self.path_to_list(start, path)

    def path_navigator(self, start: NavigationMenu, path: list):
        if len(path) == 0:
            return start

        idx = path.pop(0)
        if start.children:
            try:
                idx = int(idx)
                child = start.children[idx - 1]
            except (ValueError, IndexError):
                raise NavigationInvalidChoice('Invalid selection')
        else:
            return start

        return self.path_navigator(child, path)

    def _redis_processing(self, state: dict):
        if state is None:
            return
        logger.info(f'redis state: {state}')
        del_keys = [key for key in state.keys() if state[key] is None]
        other_keys = [key for key in state.keys() if state[key] is not None]
        if del_keys:
            r.hdel(self.redis_key, *del_keys)

        if other_keys:
            for key in other_keys:
                if type(state[key]) in [str, int, bytes, float]:
                    r.hset(self.redis_key, key, state[key])
                elif type(state[key]) in [dict, tuple, list]:
                    try:
                        r.hset(self.redis_key, json.dumps(state[key]))
                    except Exception as e:
                        logger.warning('Error saving state data to redis: ')
                        logger.warning(e)
                else:
                    logger.warning(f"cannot save data of type {state[key].__class__.__name__} to redis")

    def navigate(self):
        step = r.hget(self.redis_key, 'FORM_STEP')
        step = int(step) if step is not None else 0
        last_input = self.ussd_string.split("*")[-1]

        processed_path = self.get_processed_path()

        # append current input to processed_path
        # NOTE: when processed_path will be passed through path_navigator function, it will be sanitized to
        # point to the menu

        if last_input:
            processed_path.append(last_input)

        def _menu(path, add_last_input=True):
            pro_path = self.path_processor(path.copy())
            _menu_ref = self.path_navigator(self.home_menu, pro_path.copy())
            # path = self.path_to_list(_menu_ref)
            # logger.info(f"MENU REF: {_menu_ref}  ::  PATH: {path}")
            r.hset(self.redis_key, 'PROCESSED_PATH', json.dumps(pro_path))
            _resp, _state, valid_input, = getattr(_menu_ref, 'get_menu')(last_input if add_last_input else None, step=step)
            if valid_input is not None and not valid_input:
                r.hset(self.redis_key, 'PROCESSED_PATH', json.dumps(pro_path[:-1]))
            self._redis_processing(_state)
            return _resp

        try:
            resp = _menu(processed_path)
            r.hset(self.redis_key, 'LAST_SUCCESS_RESPONSE', resp)
        except FormBackError:
            # we pop the last path since it was pointing to a form, and now we can't go back further in the form
            # , so we also pop the path that led us to the form,
            # for that we also set FORM_STEP to None, which will later be deleted, since we are not navigating
            # in the form
            processed_path = self.get_processed_path()
            processed_path.pop()
            self._redis_processing({'FORM_STEP': None})
            r.hset(self.redis_key, 'PROCESSED_PATH', json.dumps(processed_path))
            resp = _menu(processed_path, add_last_input=False)
            r.hset(self.redis_key, 'LAST_SUCCESS_RESPONSE', resp)

        except NavigationBackError:
            # we are going back inside navigation
            processed_path = self.get_processed_path()
            r.hset(self.redis_key, 'PROCESSED_PATH', json.dumps(processed_path))
            resp = _menu(processed_path, add_last_input=False)
            r.hset(self.redis_key, 'LAST_SUCCESS_RESPONSE', resp)
        except NavigationInvalidChoice:
            last_resp = r.hget(self.redis_key, "LAST_SUCCESS_RESPONSE")

            resp = f'CON Invalid Choice\n{last_resp[4:] if last_resp[:2] in ["CON", "END"] else ""}'
        return resp

    def get_processed_path(self):
        processed_path = r.hget(self.redis_key, 'PROCESSED_PATH')
        if not processed_path:
            processed_path = "[]"
        try:
            processed_path = json.loads(processed_path)
        except Exception as e:
            logger.warning("invalid processed path variable... ")
            logger.warning(e)
            processed_path = []
        return processed_path
