import datetime as dt
import locale
import os
import shutil
import tempfile
from io import StringIO
from unittest import skipUnless
from urllib.parse import urlparse

from django.conf import settings
from django.core import mail
from django.http import HttpResponse
from django.test import TestCase, override_settings

import numpy as np
from bs4 import BeautifulSoup
from django_selenium_clean import PageElement
from freezegun import freeze_time
from selenium.webdriver.common.by import By

from enhydris.tests.test_views import SeleniumTestCase
from enhydris_synoptic import models
from enhydris_synoptic.tasks import create_static_files

from .data import TestData


class RandomSynopticRoot(override_settings):
    """
    Override ENHYDRIS_SYNOPTIC_ROOT to a temporary directory.

    Specifying "@RandomSynopticRoot()" as a decorator is the same as
    "@override_settings(ENHYDRIS_SYNOPTIC_ROOT=tempfile.mkdtemp())", except
    that in the end it removes the temporary directory.
    """

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp()
        super(RandomSynopticRoot, self).__init__(ENHYDRIS_SYNOPTIC_ROOT=self.tmpdir)

    def disable(self):
        super(RandomSynopticRoot, self).disable()
        shutil.rmtree(self.tmpdir)


def days_since_epoch(y, mo, d, h, mi):
    adelta = dt.datetime(y, mo, d, h, mi) - dt.datetime(1, 1, 1)
    return adelta.days + 1 + adelta.seconds / 86400.0


class AssertHtmlContainsMixin:
    def assertHtmlContains(self, filename, text):
        """Check if a file contains an HTML extract.

        This is pretty much the same as self.assertContains() with html=True,
        but uses a filename instead of a response.
        """
        # We implement it by converting to an HTTPResponse, because there is
        # no better way to use self.assertContains() to do the actual job.
        with open(filename, encoding="utf-8") as f:
            response = HttpResponse(f.read())
        self.assertContains(response, text, html=True)


@RandomSynopticRoot()
class ChartTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = TestData()
        settings.TEST_MATPLOTLIB = True
        create_static_files()

    @classmethod
    def tearDownClass(self):
        settings.TEST_MATPLOTLIB = False
        super().tearDownClass()

    def test_chart(self):
        # We will not compare a bitmap because it is unreliable; instead, we
        # will verify that an image was created and that the data that was used
        # in the image creation was correct. See
        # http://stackoverflow.com/questions/27948126#27948646

        # Check that it is a png of substantial length
        filename = os.path.join(
            settings.ENHYDRIS_SYNOPTIC_ROOT, "chart", str(self.data.stsg2_2.id) + ".png"
        )
        self.assertTrue(filename.endswith(".png"))
        self.assertGreater(os.stat(filename).st_size, 100)

        # Retrieve data
        datastr = open(filename.replace("png", "dat")).read()
        self.assertTrue(datastr.startswith("(array("))
        datastr = datastr.replace("array", "np.array")
        data_array = eval(datastr)

        # Check that the data is correct
        desired_result = np.array(
            [
                [days_since_epoch(2015, 10, 23, 15, 00), 40],
                [days_since_epoch(2015, 10, 23, 15, 10), 39],
                [days_since_epoch(2015, 10, 23, 15, 20), 38.5],
            ]
        )
        np.testing.assert_allclose(data_array, desired_result)

    def test_grouped_chart(self):
        # Here we test the wind speed chart, which is grouped with wind gust.
        # See the comment in test_chart() above; the same applies here.

        # Check that it is a png of substantial length
        filename = os.path.join(
            settings.ENHYDRIS_SYNOPTIC_ROOT, "chart", str(self.data.stsg1_3.id) + ".png"
        )
        self.assertTrue(filename.endswith(".png"))
        self.assertGreater(os.stat(filename).st_size, 100)

        # Retrieve data
        datastr = open(filename.replace("png", "dat")).read()
        self.assertTrue(datastr.startswith("(array("))
        datastr = datastr.replace("array", "np.array")
        data_array = eval(datastr)

        desired_result = (
            np.array(
                [
                    [days_since_epoch(2015, 10, 22, 15, 00), 3.7],
                    [days_since_epoch(2015, 10, 22, 15, 10), 4.5],
                    [days_since_epoch(2015, 10, 22, 15, 20), 4.1],
                ]
            ),
            np.array(
                [
                    [days_since_epoch(2015, 10, 22, 15, 00), 2.9],
                    [days_since_epoch(2015, 10, 22, 15, 10), 3.2],
                    [days_since_epoch(2015, 10, 22, 15, 20), 3],
                ]
            ),
        )
        np.testing.assert_allclose(data_array[0], desired_result[0])
        np.testing.assert_allclose(data_array[1], desired_result[1])


@RandomSynopticRoot()
class StationReportTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = TestData()
        create_static_files()
        filename = os.path.join(
            settings.ENHYDRIS_SYNOPTIC_ROOT,
            cls.data.sg1.slug,
            "station",
            str(cls.data.sgs_agios.station.id),
            "index.html",
        )
        with open(filename) as f:
            cls.soup = BeautifulSoup(f, "html.parser")
        cls.labels = cls.soup.find("dl").find_all("dt")
        cls.values = cls.soup.find("dl").find_all("dd")

    def _check(self, i, expected_label, expected_value):
        self.assertEqual(self.labels[i].contents[0].strip(), expected_label)
        self.assertEqual(self.values[i].contents[0].strip(), expected_value)

    def test_date(self):
        self._check(0, "Last update", "23 Oct 2015 15:20 EET (+0200)")

    def test_rain(self):
        self._check(2, "Rain", "0.2 mm")

    def test_temperature(self):
        self._check(3, "Air temperature", "38.5 °C")

    def test_wind(self):
        self._check(4, "Wind speed", "")


@RandomSynopticRoot()
class AsciiSystemLocaleTestCase(TestCase, AssertHtmlContainsMixin):
    def setUp(self):
        self.saved_locale = locale.setlocale(locale.LC_CTYPE)
        locale.setlocale(locale.LC_CTYPE, "C")
        self.data = TestData()

    def tearDown(self):
        locale.setlocale(locale.LC_CTYPE, self.saved_locale)

    def test_uses_utf8_regardless_locale_setting(self):
        create_static_files()
        filename = os.path.join(
            settings.ENHYDRIS_SYNOPTIC_ROOT,
            self.data.sg1.slug,
            "station",
            str(self.data.sgs_agios.station.id),
            "index.html",
        )
        self.assertHtmlContains(filename, "Άγιος Αθανάσιος")


@skipUnless(getattr(settings, "SELENIUM_WEBDRIVERS", False), "Selenium is unconfigured")
class MapTestCase(SeleniumTestCase):

    komboti_div_icon = PageElement(
        By.XPATH,
        '//div[contains(@class, "leaflet-div-icon") and .//a/text()="Komboti"]',
    )
    layer_control = PageElement(By.XPATH, '//a[@class="leaflet-control-layers-toggle"]')
    layer_control_rain = PageElement(
        By.XPATH,
        (
            '//label[input[@class="leaflet-control-layers-selector"] '
            'and span/text()=" Rain"]'
        ),
    )
    layer_control_temperature = PageElement(
        By.XPATH,
        (
            '//label[input[@class="leaflet-control-layers-selector"] '
            'and span/text()=" Air temperature"]'
        ),
    )
    layer_control_wind_gust = PageElement(
        By.XPATH,
        (
            '//label[input[@class="leaflet-control-layers-selector"] '
            'and span/text()=" Wind (gust)"]'
        ),
    )

    def setUp(self):
        self.data = TestData()
        settings.TEST_MATPLOTLIB = True
        self._setup_synoptic_root()

    def tearDown(self):
        self._teardown_synoptic_root()
        settings.TEST_MATPLOTLIB = False

    def _setup_synoptic_root(self):
        # We create synoptic root inside static files so that it will be served by
        # the live server during testing (otherwise relative links to js/css/etc won't
        # work)
        this_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(this_dir)
        static_dir = os.path.join(parent_dir, "static")
        self.synoptic_root = os.path.join(static_dir, "synoptic")
        if os.path.exists(self.synoptic_root):
            raise Exception(
                (
                    "Directory {} exists; cowardly refusing to remove it. Delete it "
                    "before running the unit tests."
                ).format(self.synoptic_root)
            )
        self.saved_synoptic_root = settings.ENHYDRIS_SYNOPTIC_ROOT
        settings.ENHYDRIS_SYNOPTIC_ROOT = self.synoptic_root

    def _teardown_synoptic_root(self):
        settings.ENHYDRIS_SYNOPTIC_ROOT = self.saved_synoptic_root
        shutil.rmtree(self.synoptic_root)

    @freeze_time("2015-10-22 14:20:01")
    def test_outdated_date_shows_red(self):
        create_static_files()
        self.selenium.get(
            "{}/static/synoptic/{}/index.html".format(
                self.live_server_url, self.data.sg1.slug
            )
        )
        self.komboti_div_icon.wait_until_is_displayed()
        date = self.komboti_div_icon.find_element_by_tag_name("span")
        self.assertEqual(date.get_attribute("class"), "date old")

    @freeze_time("2015-10-22 14:19:59")
    def test_up_to_date_date_shows_green(self):
        create_static_files()
        self.selenium.get(
            "{}/static/synoptic/{}/index.html".format(
                self.live_server_url, self.data.sg1.slug
            )
        )
        self.komboti_div_icon.wait_until_is_displayed()
        date = self.komboti_div_icon.find_element_by_tag_name("span")
        self.assertEqual(date.get_attribute("class"), "date recent")

    @freeze_time("2015-10-22 14:19:59")
    def test_date_format(self):
        create_static_files()
        self.selenium.get(
            "{}/static/synoptic/{}/index.html".format(
                self.live_server_url, self.data.sg1.slug
            )
        )
        self.komboti_div_icon.wait_until_is_displayed()
        date = self.komboti_div_icon.find_element_by_tag_name("span")
        self.assertEqual(date.text, "22 Oct 2015 14:20")

    def test_value_status(self):
        create_static_files()
        self.selenium.get(
            "{}/static/synoptic/{}/index.html".format(
                self.live_server_url, self.data.sg1.slug
            )
        )
        self.layer_control.wait_until_is_displayed()
        self.layer_control.click()
        self.layer_control_rain.wait_until_is_displayed()

        # Rain should be ok
        self.layer_control_rain.click()
        value = self.komboti_div_icon.find_elements_by_tag_name("span")[1]
        self.assertEqual(value.get_attribute("class"), "value ok")

        # Wind gust should be high
        self.layer_control_wind_gust.click()
        value = self.komboti_div_icon.find_elements_by_tag_name("span")[1]
        self.assertEqual(value.get_attribute("class"), "value high")

        # Temperature should be low
        self.layer_control_temperature.click()
        value = self.komboti_div_icon.find_elements_by_tag_name("span")[1]
        self.assertEqual(value.get_attribute("class"), "value low")

    @override_settings(ENHYDRIS_SYNOPTIC_STATION_LINK_TARGET="/hello{station.id}world")
    def test_station_link_target(self):
        create_static_files()
        self.selenium.get(
            f"{self.live_server_url}/static/synoptic/{self.data.sg1.slug}/index.html"
        )
        self.komboti_div_icon.wait_until_is_displayed()
        a_element = self.komboti_div_icon.find_element_by_tag_name("a")
        href = a_element.get_attribute("href")
        self.assertEqual(
            urlparse(href).path, f"/hello{self.data.station_komboti.id}world"
        )


@RandomSynopticRoot()
class EmptyTimeseriesTestCase(TestCase):
    def setUp(self):
        self.data = TestData()
        settings.TEST_MATPLOTLIB = True
        self.data.tsg_komboti_temperature.default_timeseries.set_data(StringIO(""))
        create_static_files()

    def test_chart(self):
        # Check that the chart is a png of substantial length
        filename = os.path.join(
            settings.ENHYDRIS_SYNOPTIC_ROOT, "chart", str(self.data.stsg1_2.id) + ".png"
        )
        self.assertTrue(filename.endswith(".png"))
        self.assertGreater(os.stat(filename).st_size, 100)

        # Check that the array was made from empty data
        datastr = open(filename.replace("png", "dat")).read()
        self.assertEqual(datastr, "()")


@RandomSynopticRoot()
class TimeseriesWithOneRecordTestCase(TestCase):
    def setUp(self):
        self.data = TestData()
        settings.TEST_MATPLOTLIB = True
        self.data.tsg_komboti_temperature.default_timeseries.set_data(
            StringIO("2015-10-22 15:10,0,\n")
        )
        create_static_files()

    def test_chart(self):
        # Check that the chart is a png of substantial length
        filename = os.path.join(
            settings.ENHYDRIS_SYNOPTIC_ROOT, "chart", str(self.data.stsg1_2.id) + ".png"
        )
        self.assertTrue(filename.endswith(".png"))
        self.assertGreater(os.stat(filename).st_size, 100)

        # Check that the array was made from empty data
        datastr = open(filename.replace("png", "dat")).read()
        self.assertEqual(datastr, "()")


@RandomSynopticRoot()
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailTestCase(TestCase):
    def setUp(self):
        self.data = TestData()

    def _set_limits(self, low_temperature, high_gust):
        self.data.stsg1_4.high_limit = high_gust
        self.data.stsg1_4.save()
        self.data.stsg1_2.low_limit = low_temperature
        self.data.stsg1_2.save()

    def test_sends_email_if_emails_are_registered(self):
        models.EarlyWarningEmail.objects.create(
            synoptic_group=self.data.sg1, email="someone@blackhole.com"
        )
        create_static_files()
        self.assertEqual(len(mail.outbox), 1)

    def test_does_not_send_email_if_no_emails_are_registered(self):
        create_static_files()
        self.assertEqual(len(mail.outbox), 0)

    def test_does_not_send_email_if_limits_are_not_exceeded(self):
        models.EarlyWarningEmail.objects.create(
            synoptic_group=self.data.sg1, email="someone@blackhole.com"
        )
        self._set_limits(low_temperature=10, high_gust=5)
        create_static_files()
        self.assertEqual(len(mail.outbox), 0)


@RandomSynopticRoot()
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@enhydris.com",
)
class EmailContentTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.data = TestData()
        models.EarlyWarningEmail.objects.create(
            synoptic_group=cls.data.sg1, email="someone@blackhole.com"
        )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_static_files()
        cls.message = mail.outbox[0].message()

    def test_subject(self):
        self.assertEqual(self.message["Subject"], "Enhydris early warning (Komboti)")

    def test_from(self):
        self.assertEqual(self.message["From"], "noreply@enhydris.com")

    def test_to(self):
        self.assertEqual(self.message["To"], "someone@blackhole.com")

    def test_payload(self):
        self.assertEqual(
            self.message.get_payload(),
            "Komboti 2015-10-22 15:20 Air temperature 17.0 (low limit 17.1)\n"
            "Komboti 2015-10-22 15:20 Wind 4.1 (high limit 4.0)\n",
        )


class EmailSubjectTestCase(TestCase):
    def test_subject(self):
        synoptic_group = models.SynopticGroup()
        synoptic_group.early_warnings = {
            "one": {"station": "Komboti"},
            "two": {"station": "Agios Spyridon"},
        }
        expected_subject = "Enhydris early warning (Agios Spyridon, Komboti)"
        self.assertEqual(synoptic_group._get_warning_email_subject(), expected_subject)
