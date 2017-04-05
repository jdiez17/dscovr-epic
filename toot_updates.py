# coding=utf-8
from __future__ import division, absolute_import, print_function, unicode_literals
from time import sleep
from datetime import datetime, timedelta
from requests.exceptions import ConnectionError
from configparser import ConfigParser
from geonames import GeoNamesGeocoder
from epic import EPIC
from processing import process_image
from mastodon import Mastodon

import tempfile
import logging
import pickle


def suffix(d):
    return 'th' if 11 <= d <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d % 10, 'th')


class TootEPIC(object):
    def __init__(self):
        self.log = logging.getLogger(__name__)
        self.config = ConfigParser()

        self.mastodon = Mastodon(
            client_id='toot_cred.txt',
            access_token='dscovr_cred.txt',
            api_base_url='https://social.jdiez.me')
        self.epic = EPIC()
        self.geocoder = GeoNamesGeocoder()
        self.state = {'image_queue': {},
                      'last_posted_image': datetime(2015, 9, 1),
                      'last_post_time': datetime(2015, 9, 1)}
        self.post_interval = timedelta(minutes=60)

    def poll(self):
        try:
            most_recent = sorted(self.state['image_queue'].keys())[-1]
        except IndexError:
            most_recent = self.state['last_posted_image']

        try:
            images = self.epic.get_recent_images(most_recent, 20)
        except (ConnectionError, ValueError):
            self.log.exception("Unable to fetch images")
            images = []

        added = 0
        for image in images:
            if (image['date'] not in self.state['image_queue'] and
               image['date'] > self.state['last_posted_image']):
                self.state['image_queue'][image['date']] = image
                added += 1
        if added > 0:
            self.log.info("Added %s images to queue", added)

        if len(self.state['image_queue']) > 20:
            to_drop = len(self.state['image_queue']) - 20
            self.log.info("Dropping %s images from queue", to_drop)
            for key in sorted(self.state['image_queue'])[0:to_drop]:
                del self.state['image_queue'][key]

        if len(self.state['image_queue']) > 12:
            interval = timedelta(minutes=45)
        else:
            interval = self.post_interval

        if self.state['last_post_time'] < (datetime.now() - interval) \
           and len(self.state['image_queue']) > 0:
            try:
                self.do_toot()
            except ConnectionError:
                self.log.exception("Unable to fetch image file")
            self.save_state()

    def do_toot(self):
        image_date = sorted(self.state['image_queue'].keys())[0]
        image = self.state['image_queue'][image_date]
        self.log.info("Tweeting an image")

        with tempfile.NamedTemporaryFile(suffix='.png') as imagefile:
            self.fetch_image(image, imagefile)
            self.post_toot(image, imagefile)

        del self.state['image_queue'][image_date]
        self.state['last_posted_image'] = image_date
        self.state['last_post_time'] = datetime.now()
        self.log.info("One image tooted, %s left in queue", len(self.state['image_queue']))

    def post_toot(self, image, imagefile):
        lat = image['centroid_coordinates']['lat']
        lon = image['centroid_coordinates']['lon']
        self.log.info("Geocoding %s, %s", lat, lon)

        geocoded = self.geocoder.geocode(lat, lon)
        if geocoded[0] is not None:
            place = " ".join(geocoded)

        datestring = "%s %s%s" % (image['date'].strftime("%H:%M on %A %B"),
                                  image['date'].day, suffix(image['date'].day))

        if geocoded[0] is not None:
            text = "%s, %s" % (datestring, place)
        else:
            text = datestring

        media = self.mastodon.media_post(imagefile.name)
        self.mastodon.status_post(text, media_ids=[media['id']])

    def fetch_image(self, image, destfile):
        with tempfile.NamedTemporaryFile(suffix='.png') as downloadfile:
            self.epic.download_image(image['image'], downloadfile)
            process_image(downloadfile.name, destfile.name)

    def run(self):
        logging.basicConfig(level=logging.INFO)

        try:
            with open("./state.pickle", "rb") as f:
                self.state = pickle.load(f)
        except IOError:
            self.log.exception("Failure loading state file, resetting")

        self.log.info("Running")
        try:
            while True:
                self.poll()
                sleep(120)
        finally:
            self.save_state()
            self.log.info("Shut down.")

    def save_state(self):
        self.log.info("Saving state...")
        with open("./state.pickle", "wb") as f:
            pickle.dump(self.state, f, pickle.HIGHEST_PROTOCOL)

TootEPIC().run()
