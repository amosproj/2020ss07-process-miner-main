"""
Module used for retrieving log entries and storing them for later analysis.
"""
import csv
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple

from . import graylog_access
from .graylog_access import GraylogAccess
from .log_filter import LogFilter

log = logging.getLogger(__name__)

TIMESTAMP_FILENAME = 'last_included_timestamp'
EXPORTED_FIELDS = ['correlationId', 'timestamp', 'message']
ADDED_FIELDS = ['approach', 'consent']

APPROACHES = {'redirect': 'approach=REDIRECT',
              'embedded': 'approach=EMBEDDED',
              'OAuth': 'approach=OAUTH',
              'Decoupled': 'approach=DECOUPLED'}

CONSENT = {'GET_ACCOUNTS': 'get_accounts',
           'get account list': 'get_accounts',
           'GET_TRANSACTIONS': 'get_transactions',
           'get transaction list': 'get_transactions'}

MISSING_VALUE = 'not available'


def _get_advanced_timestamp(timestamp: datetime) -> datetime:
    return timestamp + timedelta(milliseconds=1)


def _read_timestamp(path: Path) -> str:
    with path.open('r') as file:
        return file.readline()


def _write_timestamp(timestamp: str, path: Path) -> None:
    with path.open("w") as timestamp_file:
        timestamp_file.write(timestamp)


def _sanitize_filename(filename: str) -> str:
    #  Windows does not support ':' as part of filenames as it is a
    #  reserved character. There are more invalid characters but for now
    #  this should do.
    return filename.replace(':', '_')


def _add_approach(grouped_dict) -> None:
    """
    add approach value to grouped dictionary before convert to csv
    """
    approach_list = dict()
    for (correlation_id, log_entries) in grouped_dict.items():
        unlabeled = True
        for entry in log_entries:
            # searching for signal words in message
            # if signal word is found it will be added to a approach dictionary
            # identified by correlation id

            wholemessage = entry['message']

            for key, approach in APPROACHES.items():
                result = wholemessage.find(approach)
                if result != -1 and unlabeled:
                    approach_list[correlation_id] = key
                    unlabeled = False
                if result == -1 and unlabeled:
                    approach_list[correlation_id] = MISSING_VALUE

        # add to each row the approach value
        for entry in log_entries:
            entry['approach'] = approach_list[correlation_id]


def _add_consent(grouped_dict) -> None:
    """
    add consent value to grouped dictionary before convert to csv
    """
    for log_entries in grouped_dict.values():
        # search for signal words
        for entry in log_entries:
            wholemessage = entry['message']
            notlabeled = True
            for keyword, consent in CONSENT.items():
                result = wholemessage.find(keyword)
                if result != -1 and notlabeled:
                    entry['consent'] = consent
                    notlabeled = False

            # add no approach if no key word wasn't found
            if result == -1 and notlabeled:
                entry['consent'] = MISSING_VALUE


class LogRetriever:
    """
    Class used for retrieving and storing log entries.
    """
    def __init__(self, graylog: GraylogAccess, target_dir: str,
                 filter_expressions: List[str]):
        self.graylog_access = graylog
        self.log_filter = LogFilter(EXPORTED_FIELDS, 'message',
                                    filter_expressions)
        self.target_dir = Path(target_dir)

    def __str__(self) -> str:
        return f'{self.__class__.__name__} [' \
               f'graylog_access <{self.graylog_access}>, ' \
               f'log_filter <{self.log_filter}>, ' \
               f'target_dir <{self.target_dir}>]'

    def retrieve_logs(self) -> None:
        """
        Retrieves logs from the configured Graylog instance. Logs are stored
        in the configured directory grouped by their correlationID in separate
        CSV files.
        """
        self._prepare_target_dir()

        last_retrieved_timestamp = self._load_last_included_timestamp()
        first_timestamp = _get_advanced_timestamp(last_retrieved_timestamp)
        lines = self.graylog_access.get_log_entries(
            first_timestamp, EXPORTED_FIELDS)

        if not lines:
            log.info("no (new) log entries found")
            return

        fields, sorted_lines = self._convert_log_lines_to_dict(lines)
        # filter log entries before they get processed any further
        self.log_filter.filter_log_entries(sorted_lines)
        # organize/collect related log entries
        grouped_lines, last_timestamp = self._process_csv_lines(sorted_lines)
        # add approach and consent type
        _add_approach(grouped_lines)
        _add_consent(grouped_lines)

        # add additional fields that were created during log processing
        fields.extend(ADDED_FIELDS)

        self._store_logs_as_csv(grouped_lines, fields)
        self._store_last_included_timestamp(last_timestamp)

    def _prepare_target_dir(self) -> None:
        log.info('preparing target directory "%s"', self.target_dir)
        if not self.target_dir.exists():
            log.info('creating missing target directory (and parents)...')
            self.target_dir.mkdir(parents=True, exist_ok=True)

    def _load_last_included_timestamp(self) -> datetime:
        timestamp_path = self.target_dir.joinpath(TIMESTAMP_FILENAME)
        if timestamp_path.exists() and timestamp_path.is_file():
            log.info('reading last included timestamp from file "%s"',
                     timestamp_path)
            timestamp = _read_timestamp(timestamp_path)
            if graylog_access.timestamp_format_is_valid(timestamp):
                log.info('timestamp of last retrieved log entry: "%s"',
                         timestamp)
                return graylog_access.get_datetime_from_timestamp(timestamp)
            log.error('invalid timestamp format "%s"...', timestamp)
        else:
            log.info("information about last included timestamp not found in "
                     "target directory...")

        default_time = datetime.fromtimestamp(0)
        log.info("...using default timestamp '%s'", default_time)
        return default_time

    def _store_last_included_timestamp(self, timestamp) -> None:
        timestamp_path = self.target_dir.joinpath(TIMESTAMP_FILENAME)
        log.info("storing timestamp of last log entry to file '%s'",
                 timestamp_path)
        _write_timestamp(timestamp, timestamp_path)

    @staticmethod
    def _convert_log_lines_to_dict(lines: List[str]) \
            -> Tuple[List[str], List[Dict[str, str]]]:
        reader = csv.DictReader(lines)
        sorted_list = sorted(reader, key=lambda row: row['timestamp'],
                             reverse=False)
        return list(reader.fieldnames), sorted_list

    @staticmethod
    def _process_csv_lines(entries: List[Dict[str, str]]) -> Tuple[
            Dict[str, List[Dict[str, str]]], str]:
        grouped_lines = LogRetriever._group_by_correlation_id(entries)
        timestamp_of_last_entry = entries[-1]['timestamp']
        return grouped_lines, timestamp_of_last_entry

    @staticmethod
    def _group_by_correlation_id(
            lines: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
        grouped_lines = defaultdict(list)
        for line in lines:
            correlation_id = line['correlationId']
            if not correlation_id:
                log.info("omitting row with missing correlationId %s", line)
                continue
            grouped_lines[correlation_id].append(line)
        return grouped_lines

    def _store_logs_as_csv(self, grouped_dict,
                           fieldnames: List[str]) -> None:
        for (correlation_id, log_entries) in grouped_dict.items():
            first_timestamp = log_entries[0]['timestamp']
            filename = f"{first_timestamp}_{correlation_id}.csv"
            file_path = self.target_dir.joinpath(_sanitize_filename(filename))
            log.info("storing process with correlation_id '%s' in file '%s'",
                     correlation_id, file_path)

            # move message to rightmost column
            fieldnames.remove('message')
            fieldnames.append('message')
            with file_path.open("w", newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames)
                writer.writeheader()
                writer.writerows(log_entries)
