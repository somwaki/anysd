import enum
import json
import logging
import string
from itertools import count
from typing import Callable, List

import redis
from anytree import Node, NodeMixin

from .conf import config as cfg, FormBackError, r, back_symbol, home_symbol, NavigationBackError, \
    NavigationInvalidChoice, ImproperlyConfigured, ConditionEvaluationError, ConditionResultError, rc

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
        self.r = redis.Redis(**rc)
        self.ussd_string = ussd_string
        self.last_input = self.ussd_string.split("*")[-1]


class ShortCutHandler:
    pass


def get_var(msisdn, session_id, var):
    return r.hget(f'{msisdn}:{session_id}', var)


def set_var(msisdn, session_id, data):
    return r.hset(f'{msisdn}:{session_id}', mapping=data)


class ListInput:

    def __init__(self, items: List, title: str, key=None, idx=None, extra=None):
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
        self.extra = extra

    def get_items(self):
        if callable(self.items):
            self.items = self.items()
            
        if not isinstance(self.items, list):
            raise ValueError(f'self.items should be of type list, not {self.items.__class__.__name__}')
        if not self.items:
            raise ValueError(f'The list appears to be empty. ')

        if isinstance(self.items[0], (str, int, float)):
            rsp = '\n'.join([f'{idx}. {str(item)}' for idx, item in enumerate(self.items, start=1)])
            menu = f'CON {self.title}\n{rsp}'

        elif isinstance(self.items[0], dict):
            rsp = '\n'.join([f'{idx}. {item[self.key]}' for idx, item in enumerate(self.items, start=1)])
            menu = f'CON {self.title}'

        elif isinstance(self.items[0], list) or isinstance(self.items[0], tuple):
            rsp = '\n'.join([f'{idx}. {item[idx]}' for idx, item in enumerate(self.items, start=1)])
            menu = f'CON {self.title}\n{rsp}'

        else:
            raise ValueError(
                f'self.items should contain items of type str, dict, list or tuple, not {self.items[0].__class__.__name__}')

        xtra = '' if self.extra is None else f'\n{self.extra}'
        return f'{menu}{xtra}'

    def get_item(self, idx):
        if isinstance(idx, int):
            return self.items[idx - 1]
        return None

    def validate(self, key):
        try:
            key = int(key)
            if key in range(1, len(self.items) + 1):
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

    def get_step_type(self, step):
        try:
            t = type(self.form_questions[str(step)]['menu'])
        except KeyError:
            return None
        return t

    def get_step_item(self, step):
        return self.form_questions[str(step)]['menu']

    def call_post_validation(self):
        pass

    def _validate_last_input(self, current_step, last_input, msisdn, session_id):
        """
        validate by using if...else, for all steps in this flow
        :return:
        """

        _val, _extra_data = self.step_validator(current_step, last_input, msisdn=msisdn, session_id=session_id)
        if _val is None or not isinstance(_val, bool):
            logger.warning('Input not validated explicitly by validator function, Default value of True has been used')
            _val = True

        return _val, _extra_data

    def gather_form_keys(self):
        return [self.form_questions[x]['name'] for x in self.form_questions.keys()]

    def _response(self, current_step, last_input, msisdn, session_id, ussd_string):
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
            if self.get_step_type(current_step) == ListInput:
                list_ref: ListInput = self.get_step_item(current_step)
                valid_last_input = list_ref.validate(last_input)

                # handle bs logic
                _res = self._validate_last_input(
                    current_step, last_input, msisdn=msisdn, session_id=session_id)

                if isinstance(_res, tuple) and len(_res) == 2:
                    _xtra_data = _res[1]
                elif isinstance(_res, dict):
                    _xtra_data = _res
                else:
                    raise ImproperlyConfigured(
                        f"response from {self._validate_last_input}() should be a tuple of bool and dict or a dict."
                        f" Not {_res.__class__.__name__}")
            else:
                valid_last_input, _xtra_data = self._validate_last_input(
                    current_step, last_input, msisdn=msisdn, session_id=session_id)

            if _xtra_data is not None:
                if isinstance(_xtra_data, dict):
                    _state.update(_xtra_data)
                else:
                    logger.warning(f'extra_data from validation should be a dict not {_xtra_data.__class__.__name__}')

        # if last input is valid, display next menu, otherwise, show invalid input message, and display same menu
        if valid_last_input:
            if last_input not in [back_symbol, home_symbol] and current_step != 0:
                # setting last input as variable to be saved in redis
                _field_name: str = self.form_questions.get(str(current_step)).get('name')
                if _field_name and _field_name.replace("_", "").isalnum() and not _field_name[0].isnumeric():
                    if self.get_step_type(current_step) == ListInput:
                        _state[_field_name] = self.get_step_item(current_step).get_item(last_input)
                    else:
                        _state[_field_name] = last_input
                else:
                    logger.warning(
                        f'field_name "{_field_name}" is not valid. It should be contain letters, underscores and '
                        f'numbers, but begin with a letter or underscore')
                # end variable

            # call post_validation..

            if current_step != 0 and last_input not in [back_symbol, home_symbol]:
                q = self.form_questions.get(str(current_step))
                if q:
                    post_call = q.get('post_call')
                    if post_call:
                        data = {}
                        for key in self.gather_form_keys():
                            data[key] = r.hget(f'{msisdn}:{session_id}', key)
                        data[self.form_questions[str(current_step)]['name']] = last_input

                        f = post_call(msisdn, session_id, ussd_string, data)
            try:

                resp = self.form_questions[str(current_step + 1)].copy()

                # increment step here
                _state['FORM_STEP'] = current_step + 1
            except ValueError:
                resp = None

            except KeyError or IndexError:
                if current_step <= -1:
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
                raise
        else:
            _menu = self.form_questions[str(current_step)]['menu']
            if isinstance(_menu, ListInput):
                resp = self.invalid_input.format(menu=_menu.get_items()[4:])
            elif callable(_menu):
                resp = self.invalid_input.format(menu=_menu(
                    msisdn=msisdn, session_id=session_id, ussd_string=ussd_string, data={})[4:])
            else:
                resp = self.invalid_input.format(menu=_menu[4:])

        # start get the response for next menu
        if isinstance(resp['menu'], ListInput):
            resp = resp['menu'].get_items()

        elif callable(resp['menu']):
            data = {}
            for key in self.gather_form_keys():
                data[key] = r.hget(f'{msisdn}:{session_id}', key)
            if current_step != 0:
                data[self.form_questions[str(current_step + 1)]['name']] = last_input

            try:
                resp = resp['menu'](msisdn=msisdn, session_id=session_id, ussd_string=ussd_string, data=data)
            except TypeError as t:
                logger.warning(t)
                raise ImproperlyConfigured(
                    f'The callable{resp["menu"]} should accept at least 4 arguments or accept arbitrary kwargs')

        elif isinstance(resp['menu'], str):
            resp = resp['menu']

        return resp, _state, valid_last_input

    def get_response(self, current_step, last_input, msisdn, session_id, ussd_string):
        if current_step is None:
            current_step = 1

        _resp, state, valid = self._response(current_step, last_input, msisdn, session_id, ussd_string)

        return _resp, state, valid


class ConditionalFlow:
    def __init__(
            self,
            condition_fxn,
            condition_result_mapping: dict,
            cache_results=False
    ):

        self.condition_fxn = condition_fxn
        self.condition_result_mapping = condition_result_mapping
        self.cache_result = cache_results

    def __str__(self):
        return f'{self.condition_fxn}'

    def verify_result(self, result):
        if result is None:
            raise ConditionResultError('Condition Evaluation Result is None')

        if result not in self.condition_result_mapping.keys():
            raise ConditionResultError(f'Condition Evaluation Result <{result}> not in mapping keys')

    def evaluate(self, msisdn, session_id, ussd_string, last_input, redis_key, redis_conn):
        try:
            result = self.condition_fxn(
                msisdn=msisdn,
                session_id=session_id,
                ussd_string=ussd_string,
                last_input=last_input,
                redis_key=redis_key,
                redis_conn=redis_conn
            )
            self.verify_result(result)
        except ConditionResultError:
            raise
        except Exception as x:
            logger.exception(x)
            raise ConditionEvaluationError('Error when evaluating conditional function')
        return result

    def get_menu(self, msisdn, session_id, ussd_string, last_input, redis_key, redis_conn):
        result = self.evaluate(msisdn, session_id, ussd_string, last_input, redis_key, redis_conn)

        menu = self.condition_result_mapping.get(result)

        return menu


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
        self.menu_string += "" if self.parent is None else f"\n{back_symbol}: BACK  {home_symbol}: MAIN MENU"

        self.all_ids = next(self._ids)

    def _generate_menu(self, last_input, msisdn, session_id, ussd_string, step=None):
        if len(self.children) == 0 and self.next_form is not None:
            # form variable is set but it is not a FormFlow class
            if not isinstance(self.next_form, FormFlow):
                raise ValueError(
                    f"'next_form' should be of type {type(FormFlow)} not {self.next_form.__class__.__name__}")

            # Here means this Node has no children but has next_form set

            _message, _state, valid = getattr(
                self.next_form, 'get_response')(step, last_input, msisdn, session_id, ussd_string)
            self.menu_string = _message
            self.form_state = _state
            self.valid_last_input = valid

        # Node has no children and no form to call
        elif len(self.children) == 0:
            raise ValueError("Either children or next_form should be set to define next action")

        else:
            self.form_state = {'FORM_STEP': None}
            if last_input == back_symbol:
                raise NavigationBackError('We are at home')

            # Navigating through nodes. Here it means we are at a node which has children. so we will display the
            # children as menu
            self.menu_string = f"CON {self.title}:\n" + "\n".join(
                [f"{i.id}. {i.title}" for i in self.children]) if self.children else ""

        # if self.show_title: self.menu_string = f'{self.title}\n{self.menu_string[4:] if self.menu_string[0:2] in [
        # "CON", "END"] else self.menu_string}'

    def get_menu(self, last_input, msisdn, session_id, ussd_string, step=None):
        self._generate_menu(last_input, msisdn, session_id, ussd_string, step)
        return self.menu_string, self.form_state, self.valid_last_input

    def _generate_id(self):
        if self.parent is not None:
            _children = self.parent.children
            self.id = len(_children)


class NavigationController(BaseUSSD):
    def __init__(self, home_menu: NavigationMenu, msisdn, session_id, ussd_string):

        super().__init__(msisdn, session_id, ussd_string)
        self.home_menu = home_menu

    def _path_process(self, path_as_list: list = None, index=1):
        """

                :param path_as_list:
                :param index:
                :return:
                """

        path = path_as_list
        if path is None:
            path = json.loads(r.hget(self.redis_key, 'PATH_AS_LIST'))

        if path and path[0] in [str(back_symbol), str(home_symbol)]:
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
                return self._path_process(path, index - 1)

        elif path[index] == str(home_symbol):
            for i in range(index + 1):
                path.pop(0)

            if len(path) > 1:
                return self._path_process(path, index=1)
        else:
            return self._path_process(path, index + 1)

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

    def path_processor(self, path_as_list: list = None, index=1, offset: int = None):
        if offset is None:
            offset = 0

        processed_path = self._path_process(path_as_list, index)

        if len(processed_path) >= offset:
            return processed_path[offset:]
        return processed_path

    def path_navigator(self, start: NavigationMenu, path: list, **kwargs):
        if len(path) == 0 and isinstance(start, NavigationMenu):
            return start

        if isinstance(start, ConditionalFlow):
            start = start.get_menu(**kwargs)
            return self.path_navigator(start, path, **kwargs)

        idx = path.pop(0)
        if start.children:
            try:
                idx = int(idx)
                child = start.children[idx - 1]
            except (ValueError, IndexError):
                raise NavigationInvalidChoice('Invalid selection')
        else:
            return start

        return self.path_navigator(child, path, **kwargs)

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

    def navigate(self, offset=None):
        step = r.hget(self.redis_key, 'FORM_STEP')
        step = int(step) if step is not None else 0
        last_input = self.ussd_string.split("*")[-1]

        # processed_path = self.get_processed_path()
        processed_path = self.ussd_string.split("*") if self.ussd_string else []

        # append current input to processed_path
        # NOTE: when processed_path will be passed through path_navigator function, it will be sanitized to
        # point to the menu

        # if last_input:
        #     processed_path.append(last_input)

        def _menu(path, add_last_input=True, offset=None):
            pro_path = self.path_processor(path.copy(), offset=offset)
            logger.info(f"PROCESSED_PATH: {pro_path}")

            data = {
                'msisdn': self.msisdn,
                'session_id': self.session_id,
                'ussd_string': self.ussd_string,
                'last_input': self.last_input,
                'redis_key': self.redis_key,
                'redis_conn': r
            }
            _menu_ref = self.path_navigator(self.home_menu, pro_path.copy(), **data)
            # path = self.path_to_list(_menu_ref)
            # logger.info(f"MENU REF: {_menu_ref}  ::  PATH: {path}")
            r.hset(self.redis_key, 'PROCESSED_PATH', json.dumps(pro_path))

            _resp, _state, valid_input, = getattr(_menu_ref, 'get_menu')(
                last_input if add_last_input else None,
                self.msisdn,
                self.session_id,
                self.ussd_string,
                step=step
            )

            if valid_input is not None and not valid_input:
                r.hset(self.redis_key, 'PROCESSED_PATH', json.dumps(pro_path[:-1]))
            self._redis_processing(_state)
            return _resp

        try:
            resp = _menu(processed_path, offset=offset)
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
            resp = _menu(processed_path, add_last_input=False, offset=offset)
            r.hset(self.redis_key, 'LAST_SUCCESS_RESPONSE', resp)

        except NavigationBackError:
            # we are going back inside navigation
            processed_path = self.get_processed_path()
            r.hset(self.redis_key, 'PROCESSED_PATH', json.dumps(processed_path))
            resp = _menu(processed_path, add_last_input=False, offset=offset)
            r.hset(self.redis_key, 'LAST_SUCCESS_RESPONSE', resp)
        except NavigationInvalidChoice:
            last_resp = r.hget(self.redis_key, "LAST_SUCCESS_RESPONSE")

            resp = f'CON Invalid Choice\n{last_resp[4:] if last_resp and last_resp[:3] in ["CON", "END"] else ""}'

        resp: str = self.format_response(resp)
        logger.info(f'Response :: {resp}')
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

    def format_response(self, resp):
        items = [tup[1] for tup in string.Formatter().parse(resp) if tup[1] is not None]
        kwargs = {}
        for item in items:
            kwargs[item] = r.hget(self.redis_key, item)

        logger.info(f'KWARGS :: {kwargs}')
        resp = resp.format(**kwargs)
        return resp
