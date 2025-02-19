import logging
import json
import sys
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
import requests
from pydukeenergy.meter import Meter
from pprint import pformat
from typing import Optional


BASE_URL = "https://www.duke-energy.com/"
LOGIN_URL = BASE_URL + "facade/api/Authentication/SignIn"
USAGE_ANALYSIS_URL = BASE_URL + "api/UsageAnalysis/"
BILLING_INFORMATION_URL = USAGE_ANALYSIS_URL + "GetBillingInformation"
METER_ACTIVE_URL = BASE_URL + "my-account/usage-analysis"
USAGE_CHART_URL = USAGE_ANALYSIS_URL + "GetUsageChartData"

USER_AGENT = {"User-Agent": "python/{}.{} pyduke-energy/0.0.6"}
LOGIN_HEADERS = {"Content-Type": "application/json"}
USAGE_ANALYSIS_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"}

_LOGGER = logging.getLogger(__name__)


class DukeEnergy(object):
    """
    API interface object.
    """

    def __init__(self, email, password, update_interval=60):
        """
        Create the Duke Energy API interface object.
        Args:
            email (str): Duke Energy account email address.
            password (str): Duke Energy account password.
            update_interval (int): How often an update should occur. (Min=10)
        """
        global USER_AGENT, LOGIN_HEADERS
        version_info = sys.version_info
        major = version_info.major
        minor = version_info.minor
        USER_AGENT["User-Agent"] = USER_AGENT["User-Agent"].format(major, minor)
        LOGIN_HEADERS.update(USER_AGENT)
        self.email = email
        self.password = password
        self.meters = []
        self.session = requests.Session()
        self.session.headers.update(LOGIN_HEADERS)
        self.update_interval = update_interval
        if not self._login():
            raise DukeEnergyException("")
        if not self.get_account_number():
            raise DukeEnergyException("")

    def get_meters(self):
        self._get_meters()
        return self.meters

    def get_billing_info(self, meter):
        """
        Pull the billing info for the meter.
        """
        if self.session.cookies or self._login():
            post_body = {"MeterNumber": meter.type + " - " + meter.id}
            headers = USAGE_ANALYSIS_HEADERS.copy()
            headers.update(USER_AGENT)
            response = self.session.post(BILLING_INFORMATION_URL, data=json.dumps(post_body), headers=headers,
                                         timeout=10)
            _LOGGER.debug(str(response.content))
            try:
                if response.status_code != 200:
                    _LOGGER.error("Billing info request failed: " + response.status_code)
                    self._logout()
                    return False
                if response.json()["Status"] == "ERROR":
                    self._logout()
                    return False
                if response.json()["Status"] == "OK":
                    meter.set_billing_usage(response.json()["Data"][-1])
                    return True
                else:
                    _LOGGER.error("Status was {}".format(response.json()["Status"]))
                    self._logout()
                    return False
            except Exception as e:
                _LOGGER.exception("Something went wrong. Logging out and trying again.")
                self._logout()
                return False

    def get_usage_chart_data(self, meter):
        """
        billing_frequency ["Week", "Billing Cycle", "Month"]
        graph ["hourlyEnergyUse", "DailyEnergy", "averageEnergyByDayOfWeek"]
        """
        if datetime.today().weekday() == 6:
            the_date = meter.date - timedelta(days=1)
        else:
            the_date = meter.date
        if self.session.cookies or self._login():
            post_body = {
                "Graph": "DailyEnergy",
                "BillingFrequency": "Week",
                "GraphText": "Daily Energy and Avg. ",
                "Date": the_date.strftime("%m / %d / %Y"),
                "MeterNumber": meter.type + " - " + meter.id,
                "ActiveDate": meter.start_date
            }
            headers = USAGE_ANALYSIS_HEADERS.copy()
            headers.update(USER_AGENT)
            response = self.session.post(USAGE_CHART_URL, data=json.dumps(post_body), headers=headers, timeout=10)
            _LOGGER.debug(str(response.content))
            try:
                if response.status_code != 200:
                    _LOGGER.error("Usage data request failed: " +
                                  response.status_code)
                    self._logout()
                    return False
                if response.json()["Status"] == "ERROR":
                    self._logout()
                    return False
                if response.json()["Status"] == "OK":
                    meter.set_chart_usage(response.json())
                    return True
                else:
                    self._logout()
                    return False
            except Exception as e:
                _LOGGER.exception("Something went wrong. Logging out and trying again.")
                self._logout()
                return False

    def _post(self, url, payload) -> requests.models.Response:
        if isinstance(payload, dict):
            response = self.session.post(url, json=payload,
                                         headers=LOGIN_HEADERS,
                                         timeout=10,
                                         allow_redirects=False)
        elif isinstance(payload, str):
            response = self.session.post(url, data=payload,
                                         headers=LOGIN_HEADERS,
                                         timeout=10,
                                         allow_redirects=False)
        else:
            _LOGGER.error("Unsupported type of payload: %s", type(payload))
            raise DukeEnergyPostException
        if response.status_code != 200:
            _LOGGER.debug("Status code %d", response.status_code)
            raise DukeEnergyPostException
        return response

    def _post_and_check_json_status(self, url, payload) -> Optional[dict]:
        response = self._post(url, payload)
        if response.json():
            json = response.json()
            if 'Status' in json.keys():
                if json['Status'] == "Success":
                    return response.json()
                else:
                    _LOGGER.debug("Returned Status is '%s'", json['Status'])
                    if 'MessageText' in json.keys():
                        _LOGGER.debug("MessageText = '%s'",
                                      json['MessageText'])
            else:
                _LOGGER.debug("Returned JSON doesn't have 'Status' key.\n %s",
                              pformat(json))
        else:
            # trim response text to 400 chars
            _LOGGER.debug("Response is not JSON:\n%s",response.text[:400])
        return None

    def _login(self) -> bool:
        """
        Authenticate. This creates a cookie on the session which is used to authenticate with
        the other calls. Unfortunately the service always returns 200 even if you have a wrong
        password.
        """
        _LOGGER.debug("Logging in...")
        if not self._post_and_check_json_status(LOGIN_URL,
                    {"loginIdentity": self.email, "password": self.password}):
            _LOGGER.error("Login failed")
            return False

        # getting Accounts info.
        json = self._post_and_check_json_status(
            BASE_URL+"facade/api/AccountSelector/GetResiAccounts",
            {"email":""})
        if json:
            self.GetResiAccountsResponse = json
            if 'CdpId' in json.keys():
                self.cdp = json['CdpId']
            else:
                self.cdp = None
                _LOGGER.debug("Can't find 'CdpId' in 'GetResiAccounts' response:\n",
                              pformat(json))
        return True

    def _logout(self):
        """
        Delete the session.
        """
        _LOGGER.debug("Logging out.")
        self.session.cookies.clear()

    def _get_meters(self):
        """
        There doesn't appear to be a service to get this data.
        Collecting the meter info to build meter objects.
        """
        if self._login():
            response = self.session.get(METER_ACTIVE_URL, timeout=10)
            _LOGGER.debug(str(response.text))
            soup = BeautifulSoup(response.text, "html.parser")
            meter_data = json.loads(soup.find("duke-dropdown", {"id": "usageAnalysisMeter"})["items"])
            _LOGGER.debug(str(meter_data))
            for meter in meter_data:
                meter_type, meter_id = meter["text"].split(" - ")
                meter_start_date = meter["CalendarStartDate"]
                self.meters.append(Meter(self, meter_type, meter_id, meter_start_date, self.update_interval))
            self._logout()


    def get_account_number(self):
        """
         TODO: implement search of an account by address or name
        """
        found = 0
        for account in self.GetResiAccountsResponse['Accounts']:
            if account['Status'].upper() == "ACTIVE":
                self.account = account['AccountNum']
                found += 1
        _LOGGER.debug("Found %s accounts", found)
        if found > 0:
            _LOGGER.debug("Account Number %s", self.account)
            return True
        return False


    def get_usage_xml(self):
        if self._login:
            GET_USAGE_XML_URL = BASE_URL + "form/PlanRate/GetEnergyUsage"
            GetUsagePayload = {"request":"{\"SrcAcctId\":\"" + self.account + "\",\"SrcAcctId2\":\"\",\"SrcSysCd\":\"ISU\",\"ServiceType\":\"ELECTRIC\"}"}
            for retrynum in range(3):
                try:
                    response = self.session.post(GET_USAGE_XML_URL,
                                                 json=GetUsagePayload)
                except requests.exceptions.TooManyRedirects as e:
                    _LOGGER.debug("got redirection %s, %s", e.response.url,
                                  e.request)
                    continue
                if '<?xml ' in response.text:
                    _LOGGER.debug("got XML!")
                    break
                else:
                    _LOGGER.debug("failed to get xml")
            return response.text

class DukeEnergyException(Exception):
    pass

class DukeEnergyPostException(Exception):
    pass

