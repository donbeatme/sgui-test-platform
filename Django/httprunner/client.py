import hashlib
import json
import re
import time
from urllib.parse import unquote

import requests
import urllib3
from loguru import logger
from requests import Request, Response
from requests.exceptions import (
    InvalidSchema,
    InvalidURL,
    MissingSchema,
    RequestException,
)

from httprunner.models import RequestData, ResponseData
from httprunner.models import SessionData, ReqRespData
from httprunner.utils import lower_dict_keys, omit_long_data

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


BINARY_PREVIEW_BYTES = 64
BINARY_BODY_MESSAGE = "Binary response body omitted from execution report."

TEXT_CONTENT_TYPES = {
    "application/javascript",
    "application/x-javascript",
    "application/xml",
    "application/xhtml+xml",
    "application/x-www-form-urlencoded",
    "application/problem+json",
}

BINARY_CONTENT_TYPES = {
    "application/gzip",
    "application/msword",
    "application/octet-stream",
    "application/pdf",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-7z-compressed",
    "application/x-gzip",
    "application/x-rar-compressed",
    "application/x-tar",
    "application/x-zip-compressed",
    "application/zip",
}


class ApiResponse(Response):
    def raise_for_status(self):
        if hasattr(self, "error") and self.error:
            raise self.error
        Response.raise_for_status(self)


def _media_type(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _get_header(headers, name: str) -> str:
    target = name.lower()
    for key, value in dict(headers or {}).items():
        if str(key).lower() == target:
            return str(value)
    return ""


def _is_json_content_type(content_type: str) -> bool:
    media_type = _media_type(content_type)
    return media_type == "application/json" or media_type.endswith("+json")


def _is_text_content_type(content_type: str) -> bool:
    media_type = _media_type(content_type)
    return (
        media_type.startswith("text/")
        or media_type in TEXT_CONTENT_TYPES
        or media_type.endswith("+xml")
    )


def _extract_filename(content_disposition: str) -> str:
    if not content_disposition:
        return ""

    filename_star = re.search(
        r"""filename\*\s*=\s*(?:[^']*'')?("?)([^";]+)\1""",
        content_disposition,
        flags=re.IGNORECASE,
    )
    if filename_star:
        return unquote(filename_star.group(2).strip())

    filename = re.search(
        r"""filename\s*=\s*("?)([^";]+)\1""",
        content_disposition,
        flags=re.IGNORECASE,
    )
    if filename:
        return filename.group(2).strip()

    return ""


def _looks_like_binary(content: bytes, headers, content_type: str) -> bool:
    content = content or b""
    media_type = _media_type(content_type)
    content_disposition = _get_header(headers, "content-disposition").lower()

    if "attachment" in content_disposition:
        return True

    if media_type in BINARY_CONTENT_TYPES:
        return True

    if media_type.startswith(("audio/", "font/", "image/", "video/")):
        return True

    if not content:
        return False

    if b"\x00" in content[:1024]:
        return True

    if _is_json_content_type(content_type) or _is_text_content_type(content_type):
        return False

    return media_type.startswith("application/")


def _binary_body_summary(
    content: bytes,
    content_type: str = "",
    headers=None,
    message: str = BINARY_BODY_MESSAGE,
) -> dict:
    content = content or b""
    summary = {
        "type": "binary",
        "binary": True,
        "omitted": True,
        "message": message,
        "content_type": content_type or "",
        "content_length": len(content),
        "sha256": hashlib.sha256(content).hexdigest() if content else "",
        "preview_hex": content[:BINARY_PREVIEW_BYTES].hex(),
    }

    filename = _extract_filename(_get_header(headers, "content-disposition"))
    if filename:
        summary["filename"] = filename

    return summary


def _sanitize_text(value: str) -> str:
    return value.replace("\x00", "\\u0000")


def sanitize_json_record(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return _sanitize_text(value)

    if isinstance(value, bytes):
        return _binary_body_summary(
            value,
            message="Binary data omitted from execution report.",
        )

    if isinstance(value, dict):
        return {
            _sanitize_text(str(key)): sanitize_json_record(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [sanitize_json_record(item) for item in value]

    return repr(value)


def _record_request_body(request_body, request_headers):
    if request_body is None:
        return None

    request_content_type = lower_dict_keys(request_headers).get("content-type", "")
    if (
        request_content_type
        and "multipart/form-data" in request_content_type.lower()
    ):
        return "upload file stream (OMITTED)"

    try:
        return sanitize_json_record(json.loads(request_body))
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    except TypeError:
        return sanitize_json_record(request_body)

    if isinstance(request_body, bytes):
        if _looks_like_binary(request_body, request_headers, request_content_type):
            return _binary_body_summary(
                request_body,
                request_content_type,
                request_headers,
                message="Binary request body omitted from execution report.",
            )

        return omit_long_data(_sanitize_text(request_body.decode("utf-8", "replace")))

    return sanitize_json_record(omit_long_data(request_body))


def _record_response_body(resp_obj: Response, content_type: str, headers: dict):
    content = resp_obj.content or b""

    if not _looks_like_binary(content, headers, content_type):
        try:
            return sanitize_json_record(resp_obj.json())
        except ValueError:
            resp_text = _sanitize_text(resp_obj.text)
            return sanitize_json_record(omit_long_data(resp_text))

    return _binary_body_summary(content, content_type, headers)


def get_req_resp_record(resp_obj: Response) -> ReqRespData:
    """get request and response info from Response() object."""

    def log_print(req_or_resp, r_type):
        msg = f"\n================== {r_type} details ==================\n"
        for key, value in req_or_resp.dict().items():
            if isinstance(value, dict) or isinstance(value, list):
                value = json.dumps(value, indent=4, ensure_ascii=False)

            msg += "{:<8} : {}\n".format(key, value)
        logger.debug(msg)

    # record actual request info
    request_headers = dict(resp_obj.request.headers)
    request_cookies = resp_obj.request._cookies.get_dict()

    request_body = _record_request_body(resp_obj.request.body, request_headers)

    request_data = RequestData(
        method=resp_obj.request.method,
        url=resp_obj.request.url,
        headers=request_headers,
        cookies=request_cookies,
        body=request_body,
    )

    # log request details in debug mode
    log_print(request_data, "request")

    # record response info
    resp_headers = dict(resp_obj.headers)
    lower_resp_headers = lower_dict_keys(resp_headers)
    content_type = lower_resp_headers.get("content-type", "")

    response_body = _record_response_body(resp_obj, content_type, resp_headers)

    transport_error = getattr(resp_obj, "error", None)
    transport_error_type = None
    transport_error_message = None
    if transport_error:
        transport_error_type = transport_error.__class__.__name__
        transport_error_message = str(transport_error)
        if response_body in (None, "", b""):
            response_body = {
                "transport_error": {
                    "type": transport_error_type,
                    "message": transport_error_message,
                }
            }

    response_data = ResponseData(
        status_code=resp_obj.status_code,
        cookies=resp_obj.cookies or {},
        encoding=resp_obj.encoding,
        headers=resp_headers,
        content_type=content_type,
        body=response_body,
        error=transport_error_message,
        error_type=transport_error_type,
        is_transport_error=bool(transport_error),
    )

    # log response details in debug mode
    log_print(response_data, "response")

    req_resp_data = ReqRespData(request=request_data, response=response_data)
    return req_resp_data


class HttpSession(requests.Session):
    """
    Class for performing HTTP requests and holding (session-) cookies between requests (in order
    to be able to log in and out of websites). Each request is logged so that HttpRunner can
    display statistics.

    This is a slightly extended version of `python-request <http://python-requests.org>`_'s
    :py:class:`requests.Session` class and mostly this class works exactly the same.
    """

    def __init__(self):
        super(HttpSession, self).__init__()
        self.data = SessionData()

    def update_last_req_resp_record(self, resp_obj):
        """
        update request and response info from Response() object.
        """
        # TODO: fix
        self.data.req_resps.pop()
        self.data.req_resps.append(get_req_resp_record(resp_obj))

    def request(self, method, url, name=None, **kwargs):
        """
        Constructs and sends a :py:class:`requests.Request`.
        Returns :py:class:`requests.Response` object.

        :param method:
            method for the new :class:`Request` object.
        :param url:
            URL for the new :class:`Request` object.
        :param name: (optional)
            Placeholder, make compatible with Locust's HttpSession
        :param params: (optional)
            Dictionary or bytes to be sent in the query string for the :class:`Request`.
        :param data: (optional)
            Dictionary or bytes to send in the body of the :class:`Request`.
        :param headers: (optional)
            Dictionary of HTTP Headers to send with the :class:`Request`.
        :param cookies: (optional)
            Dict or CookieJar object to send with the :class:`Request`.
        :param files: (optional)
            Dictionary of ``'filename': file-like-objects`` for multipart encoding upload.
        :param auth: (optional)
            Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth.
        :param timeout: (optional)
            How long to wait for the server to send data before giving up, as a float, or \
            a (`connect timeout, read timeout <user/advanced.html#timeouts>`_) tuple.
            :type timeout: float or tuple
        :param allow_redirects: (optional)
            Set to True by default.
        :type allow_redirects: bool
        :param proxies: (optional)
            Dictionary mapping protocol to the URL of the proxy.
        :param stream: (optional)
            whether to immediately download the response content. Defaults to ``False``.
        :param verify: (optional)
            if ``True``, the SSL cert will be verified. A CA_BUNDLE path can also be provided.
        :param cert: (optional)
            if String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair.
        """
        self.data = SessionData()

        # timeout default to 120 seconds
        kwargs.setdefault("timeout", 120)

        # set stream to True, in order to get client/server IP/Port
        kwargs["stream"] = True

        start_timestamp = time.time()
        response = self._send_request_safe_mode(method, url, **kwargs)
        response_time_ms = round((time.time() - start_timestamp) * 1000, 2)

        try:
            client_ip, client_port = response.raw._connection.sock.getsockname()
            self.data.address.client_ip = client_ip
            self.data.address.client_port = client_port
            logger.debug(f"client IP: {client_ip}, Port: {client_port}")
        except Exception:
            pass

        try:
            server_ip, server_port = response.raw._connection.sock.getpeername()
            self.data.address.server_ip = server_ip
            self.data.address.server_port = server_port
            logger.debug(f"server IP: {server_ip}, Port: {server_port}")
        except Exception:
            pass

        # get length of the response content
        content_size = int(dict(response.headers).get("content-length") or 0)

        # record the consumed time
        self.data.stat.response_time_ms = response_time_ms
        self.data.stat.elapsed_ms = response.elapsed.microseconds / 1000.0
        self.data.stat.content_size = content_size

        # record request and response histories, include 30X redirection
        response_list = response.history + [response]
        self.data.req_resps = [
            get_req_resp_record(resp_obj) for resp_obj in response_list
        ]

        try:
            response.raise_for_status()
        except RequestException as ex:
            logger.error(f"{str(ex)}")
        else:
            logger.info(
                f"status_code: {response.status_code}, "
                f"response_time(ms): {response_time_ms} ms, "
                f"response_length: {content_size} bytes"
            )

        return response

    def _send_request_safe_mode(self, method, url, **kwargs):
        """
        Send a HTTP request, and catch any exception that might occur due to connection problems.
        Safe mode has been removed from requests 1.x.
        """
        try:
            return requests.Session.request(self, method, url, **kwargs)
        except (MissingSchema, InvalidSchema, InvalidURL):
            raise
        except RequestException as ex:
            resp = ApiResponse()
            resp.error = ex
            resp.status_code = 0  # with this status_code, content returns None
            resp.request = Request(method, url).prepare()
            return resp
