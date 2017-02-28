from st2actions.runners.pythonrunner import Action
from st2common import log as logging

import socket
import json
import time

LOG = logging.getLogger(__name__)
LS_EOL = '\n'


class StatsItem(object):
    """
    The StatsItem class is used to convert logic operators to livestatus format as well as
    return a correctly formatted string for livestatus queries.

    The idea is to allow stackstorm to supply stats listed in the following manner:

    ["state = 0", "host_up = 0", "&2"]

    The above list would produce a LiveStatus query like the following:
    Stats: state = 0
    Stats: host_up = 0
    StatsAnd: 2
    """
    def __init__(self, _item):
        self.prefix = 'Stats: '

        self.postfix = LS_EOL
        if _item.startswith('&'):
            self.prefix = "StatsAnd: "
            self._item = int(_item.split("&")[1])
        elif _item.startswith('|'):
            self.prefix = "StatsOr: "
            self._item = int(_item.split("|")[1])
        elif _item == '!':
            self.prefix = "StatsNegate:"
            self._item = ""
        else:
            self._item = _item

    def __str__(self):
        return "{}{}{}".format(self.prefix, self._item, self.postfix)


class FilterItem(object):
    """
    The FilterItem class is used to convert logic operators to livestatus format as well as
    return a correctly formatted string for livestatus queries.

    The idea is to allow stackstorm to supply stats listed in the following manner:

    ["state = 0", "host_up = 0", "&2"]

    The above list would produce a LiveStatus query like the following:
    Filter: state = 0
    Filter: host_up = 0
    And: 2
    """
    def __init__(self, _item):
        self.prefix = 'Filter: '

        self.postfix = LS_EOL
        if _item.startswith('&'):
            self.prefix = "And: "
            self._item = int(_item.split("&")[1])
        elif _item.startswith('|'):
            self.prefix = "Or: "
            self._item = int(_item.split("|")[1])
        elif _item == '!':
            self.prefix = "Negate:"
            self._item = ""
        else:
            self._item = _item

    def __str__(self):
        return "{}{}{}".format(self.prefix, self._item, self.postfix)


class TimeoutException(Exception):
    def __init__(self, msg="Execution timeout has been exceeded."):
        self.msg = msg

    def __str__(self):
        return repr(self.msg)


class LiveStatus(object):
    """
    LiveStatus class provides network access to the Live status server.
    """
    def __init__(self, host, port, max_recv=4096,
                 query_max_retries=5,
                 query_duration=5,
                 query_retry_delay=60,
                 allow_empty_list=True):
        self.host = host
        self.port = int(port)
        self.max_recv = int(max_recv)
        self.query_duration = query_duration           # unit=seconds
        self.query_retry_delay = query_retry_delay     # unit=seconds
        self.query_max_retries = query_max_retries
        self.query_attempt = 0

    def execute(self, query):
        """
        execute method sends the query to the live status server and
        returns the result.

        @query - a livestatus query.
        """
        result = None
        start = int(time.time())

        while self.query_attempt < self.query_max_retries:
            try:
                LOG.debug("Attempt number {}".format(self.query_attempt))
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.settimeout(float(self.query_duration))
                server.connect((self.host, self.port))
                LOG.debug('Sending LiveStatus query: {}'.format(query))
                server.send(query.encode('utf-8').rstrip() + LS_EOL)
                # Notify server that transmission has finished.
                server.shutdown(socket.SHUT_WR)
                buffer_ = ''
                data = server.recv(self.max_recv)
                while data:
                    buffer_ += data
                    data = server.recv(self.max_recv)
                    if (int(time.time()) - start > self.query_duration):
                        raise TimeoutException()
                result = buffer_
                server.close()
                break
            except socket.timeout as e:
                LOG.error("Timeout {}/{}".format(self.query_attempt, self.query_max_retries))
                self.query_attempt += 1
                time.sleep(self.query_retry_delay)
            except socket.error as e:
                LOG.error("Socket error occurred! {}".format(type(e)))
                self.query_attempt += 1
                time.sleep(self.query_retry_delay)
            except TimeoutException as e:
                LOG.error("Livestatus didn't respond within the time allocated.")
                self.query_attempt += 1
                time.sleep(self.query_retry_delay)
            except Exception as e:
                LOG.error("Unhandled error occurred: {}".format(sys.exc_info()))
                break
        return result

    def get_json(self, query, allow_empty_list=True):
        """
        Parse the result of a live status query as JSON.

        @query - a livestatus query. The query MUST request the result
                 is returned in JSON format.
        """
        result = None
        while self.query_attempt < self.query_max_retries:
            try:
                data = json.loads(self.execute(query))
                if allow_empty_list is not True and data == []:
                    raise ValueError()
                result = data
                break
            except TypeError as e:
                LOG.error("No or invalid data received from livestatus API")
                self.query_attempt += 1
                time.sleep(self.query_retry_delay)
            except ValueError as e:
                LOG.error("Error while deserialising JSON from livestatus API.")
                self.query_attempt += 1
                time.sleep(self.query_retry_delay)
        return result


class Get(Action):
    """
    LiveStatus Get class.
    """
    def run(self, table='', columns=None, filters=None,
            stats=None, limit=None, output_format="json",
            query_max_retries=None, query_duration=None,
            query_retry_delay=None, allow_empty_list=True):
        """
        The run method to be called by Stackstorm.

        table - the table query.
        columns - the columns to return in the dataset.
        filters - conditions to filter result set.
        stats - Calculate statistics held in data.
        limit - Not supported by Shinken.
        output_format - json or csv formats.
        """
        host = self.config['host']
        port = self.config['port']

        passthrough = {}
        if query_max_retries is not None:
            passthrough["query_max_retries"] = query_max_retries
        if query_duration is not None:
            passthrough["query_duration"] = query_duration
        if query_retry_delay is not None:
            passthrough["query_retry_delay"] = query_retry_delay

        result = (False, "An error occurred fetching data from Livestatus.")

        live_status = LiveStatus(host, port, **passthrough)
        query = 'GET {}{}'.format(table, LS_EOL)

        if columns is not None:
            query += self._process_columns(columns)

        if filters is not None:
            query += self._process_filters(filters)

        if stats is not None:
            query += self._process_stats(stats)

        if limit is not None:
            query += 'Limit: {}{}'.format(limit, LS_EOL)

        tmp = None
        if output_format.lower() == 'json':
            query += 'OutputFormat: json{}'.format(LS_EOL)
            tmp = live_status.get_json(query, allow_empty_list)
        else:
            tmp = live_status.execute(query)

        if tmp is not None:
            result = (True, tmp)

        return result

    def _process_columns(self, columns):
        """
        Convert columns list to livestatus formatted string.
        """
        return self._build_list('Columns: ', LS_EOL, [' '.join(columns)])

    def _process_filters(self, filters):
        """
        Convert filters list to livestatus formatted string.
        """
        formatted_filters = []
        try:
            for _item in filters:
                formatted_filters.append(FilterItem(_item))
        except (ValueError) as e:
            LOG.error("Incorrectly formatted logic operator in query. {}".format(filters))
        return self._build_list(items=formatted_filters)

    def _process_stats(self, stats):
        """
        Convert stats list to livestatus formatted string.
        """
        formatted_stats = []
        try:
            for _item in stats:
                formatted_stats.append(StatsItem(_item))
        except (ValueError) as e:
            LOG.error("Incorrectly formatted logic operator in query. {}".format(stats))
        return self._build_list(items=formatted_stats)

    def _build_list(self, prefix=None, postfix=None, items=[]):
        """
        Loop over list items and apply a prefix/postfix to each element.
        Returns a livestatus formatted string.
        """
        tmp = ''
        for _item in items:
            if isinstance(_item, StatsItem) or isinstance(_item, FilterItem):
                tmp += "%s" % _item
            else:
                tmp += '{}{}{}'.format(prefix, _item, postfix)
        else:
            postfix = ''
        tmp += postfix
        return tmp
