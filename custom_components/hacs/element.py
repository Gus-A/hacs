"""Element class"""
# pylint: disable=invalid-name, too-many-instance-attributes, broad-except, unused-argument
import logging
import asyncio
from datetime import datetime
import json

from homeassistant.helpers.event import async_call_later

from custom_components.hacs.const import DOMAIN_DATA
from custom_components.hacs.blueprints import HacsBase
from custom_components.hacs.exceptions import HacsBaseException

_LOGGER = logging.getLogger('custom_components.hacs')


class HacsElement(HacsBase):
    """Base HACS Element Class."""
    def __init__(self):
        pass


class Element(HacsElement):
    """Element Class"""

    def __init__(self, element_type, repo):
        """Set up a community element."""
        self.authors = []
        self.avaiable_version = None # ok
        self.description = ""
        self.element_id = repo.split("/")[-1] if "/" in repo else None # ok
        self.element_type = element_type # ok
        self.info = None # ok
        self.installed_version = None # ok
        self.isinstalled = False  # are set with actions
        self.github_last_update = None # ok
        self.manifest = None # ok-ish
        self.name = self.element_id
        self.jstype = None
        self.releases = None # ok
        self.remote_dir_location = None
        self.repo = repo # ok
        self.reason = None
        self.github_repo = None # ok
        self.github_ref = None
        self.github_last_release = None # ok
        self.github_element_content_files = [] # ok-ish
        self.github_element_content_objects = None # ok-ish
        self.github_element_content_path = None # ok-ish
        self.pending_restart = False # are set with actions
        self.pending_update = False # ok
        self.trackable = False # ok
        self.hidden = False # not implementet

    async def update_element(self, notarealargument=None):
        """Update element"""
        from custom_components.hacs.handler.storage import write_to_data_store
        start_time = datetime.now()
        if self.element_id is None:
            # Something is wrong with the element_id, don't even try.
            return

        if self.repo in self.data["commander"].blacklist:
            # This repo is marked as skippable, lets skip it.
            return

        self.trackable = True
        self.attach_github_repo_object()
        await asyncio.sleep(0.1)
        self.fetch_github_repo_data()
        await asyncio.sleep(0.1)
        self.element_has_update()
        await asyncio.sleep(0.1)
        self.fetch_github_element_content()
        await asyncio.sleep(0.1)
        if self.element_type == "integration":
            self.fetch_file_manifest()
            if self.manifest is None:
                self.skip_list_add()
        elif self.element_type == "plugin":
            self.parse_readme_for_jstype()
        await asyncio.sleep(0.1)
        self.start_task_scheduler()
        await write_to_data_store(self.hass.config.path(), self.data)
        _LOGGER.debug(f'Completed {str(self.repo)} update in {(datetime.now() - start_time).seconds} seconds')
        return True



    def attach_github_repo_object(self):
        """Attach github repo object to the element."""

        github_repo = self.github_repo
        if self.github_repo is not None:
            return

        try:
            github_repo = self.github.get_repo(self.repo)
        except Exception as error:
            _LOGGER.debug("Could not find repo for %s - %s", self.repo, error)
            self.skip_list_add()
            return
        self.github_repo = github_repo


    def start_task_scheduler(self):
        """Start task scheduler."""
        if not self.isinstalled:
            return

        # Update installed elements every 30min
        async_call_later(self.hass, 60*30, self.update_element)

    def element_has_update(self):
        """Check if the element has an update."""
        self.pending_update = bool(self.installed_version != self.avaiable_version)

    def fetch_github_repo_data(self):
        """Fetch github repo data."""
        self.fetch_github_repo_releases()
        self.fetch_github_repo_description()
        new_update = self.fetch_github_repo_last_update()

        if new_update == self.github_last_update:
            # No need to update, we have the latest info.
            return
        else:
            self.github_last_update = new_update

        self.fetch_github_repo_ref()

        if self.releases:
            # Set the latest version as the avaiable_version
            self.avaiable_version = self.github_last_release.tag_name

        self.log_repo_info()
        self.fetch_file_info()

    def fetch_github_repo_releases(self):
        """Fetch github releases."""
        self.releases = []

        all_releases = list(self.github_repo.get_releases())

        if all_releases:
            self.github_last_release = all_releases[0]

            for release in all_releases:
                self.releases.append(release.tag_name)

    def fetch_github_repo_last_update(self):
        """Fetch github last update time."""
        if self.github_last_release is not None:
            updated = self.github_last_release.created_at
        else:
            updated = self.github_repo.updated_at
        return updated.strftime("%d %b %Y %H:%M:%S")

    def fetch_github_repo_ref(self):
        """Fetch github ref."""
        if self.github_last_release is not None:
            self.github_ref = "tags/{}".format(self.github_last_release.tag_name)
        else:
            self.github_ref = self.github_repo.default_branch

    def fetch_github_repo_description(self):
        """Fetch github description."""
        self.description = "" if self.github_repo.description is None else self.github_repo.description

    def log_repo_info(self):
        """Log repository info."""
        _LOGGER.debug("------------------------------------------------------")
        _LOGGER.debug("Repository: %s", self.repo)
        _LOGGER.debug("Repository ref: %s", self.github_ref)
        _LOGGER.debug("Repository last update: %s", self.github_last_update)
        _LOGGER.debug("Repository releases: %s", self.releases)
        _LOGGER.debug("Repository last release: %s", self.avaiable_version)
        _LOGGER.debug("Repository files %s",self.github_element_content_files)

    def fetch_file_info(self):
        """Fetch info.md."""
        try:
            info = self.github_repo.get_file_contents("info.md", self.github_ref)
            info = info.decoded_content.decode()
            self.info = info
        except Exception:
            # The file probably does not exist, but that's okey, it's optional.
            pass

    def fetch_file_manifest(self):
        """Fetch manifest.json."""
        manifest_path = "{}/manifest.json".format(self.github_element_content_path)

        if manifest_path in self.github_element_content_files:
            self.manifest = self.github_repo.get_file_contents(manifest_path, self.github_ref)

            try:
                self.manifest = json.loads(self.manifest.decoded_content.decode())
            except Exception as error:
                _LOGGER.debug("Can't load manifest from %s - %s", self.repo, error)
                self.manifest = None
                self.skip_list_add()

            if self.manifest is not None:
                # Manifest exsist, lets use it
                self.authors = self.manifest["codeowners"]
                self.name = self.manifest["name"]


    def fetch_github_element_content(self):
        """Fetch element content."""
        if self.element_type == "integration":
            self.fetch_github_element_content_integration()
        elif self.element_type == "plugin":
            self.fetch_github_element_content_plugin()
        if not self.github_element_content_files:
            self.skip_list_add()



    def fetch_github_element_content_integration(self):
        """Fetch element content."""
        self.github_element_content_files = []
        try:
            if self.github_element_content_path is None:
                self.github_element_content_path = self.github_repo.get_dir_contents(
                    "custom_components", self.github_ref)[0].path
            self.github_element_content_objects = list(
                self.github_repo.get_dir_contents(
                    self.github_element_content_path, self.github_ref))

            for item in self.github_element_content_objects:
                self.github_element_content_files.append(item.path)
        except Exception:
            self.github_element_content_path = None
            self.github_element_content_objects = None
            self.github_element_content_files = []



    def fetch_github_element_content_plugin(self):
        """Fetch element content."""
        if self.github_element_content_path is None or self.github_element_content_path == "root":
            # Try fetching data from REPOROOT
            try:
                files = []
                objects = list(self.github_repo.get_dir_contents("", self.github_ref))
                for item in objects:
                    if item.name.endswith(".js"):
                        files.append(item.name)

                # Handler for plugin requirement 3
                find_file_name = "{}.js".format(self.name.replace("lovelace-", ""))
                if find_file_name in files:
                    # YES! We got it!
                    self.github_element_content_path = "root"
                    self.github_element_content_objects = objects
                    self.github_element_content_files = files
                else:
                    _LOGGER.debug("Expected filename not found in %s for %s", files, self.repo)

            except Exception:
                pass

        if self.github_element_content_path is None or self.github_element_content_path == "release":
            # Try fetching data from Release
            try:
                files = []
                objects = list(self.github_last_release.get_assets())
                for item in objects:
                    if item.name.endswith(".js"):
                        files.append(item.name)

                # Handler for plugin requirement 3
                find_file_name1 = "{}.js".format(self.name)
                find_file_name2 = "{}-bundle.js".format(self.name)
                if find_file_name1 in files or find_file_name2 in files:
                    # YES! We got it!
                    self.github_element_content_path = "release"
                    self.github_element_content_objects = objects
                    self.github_element_content_files = files
                else:
                    _LOGGER.debug("Expected filename not found in %s for %s", files, self.repo)

            except Exception:
                pass

        if self.github_element_content_path is None or self.github_element_content_path == "root":
            # Try fetching data from REPOROOT/dist
            try:
                files = []
                objects = list(self.github_repo.get_dir_contents("dist", self.github_ref))
                for item in objects:
                    if item.name.endswith(".js"):
                        files.append(item.name)

                # Handler for plug requirement 3
                find_file_name = "{}.js".format(self.name.replace("lovelace-", ""))
                if find_file_name in files:
                    # YES! We got it!
                    self.github_element_content_path = "dist"
                    self.github_element_content_objects = objects
                    self.github_element_content_files = files
                else:
                    _LOGGER.debug("Expected filename not found in %s for %s", files, self.repo)

            except Exception:
                pass


    def parse_readme_for_jstype(self):
        """Parse the readme looking for js type."""
        try:
            readme = self.github_repo.get_file_contents("README.md", self.github_ref)
            readme = readme.decoded_content.decode()
            for line in readme.splitlines():
                if "type: module" in line:
                    self.jstype = "module"
                    break
                elif "type: js" in line:
                    self.jstype = "js"
                    break
        except Exception:
            pass

    def skip_list_add(self):
        """Add repo to blacklist list."""
        _LOGGER.debug("Skipping %s on next run.", self.repo)
        self.trackable = False
        if self.repo not in self.data["commander"].blacklist:
            self.data["commander"].blacklist.append(self.repo)


    def skip_list_remove(self):
        """Add repo to skip list."""
        self.trackable = True
        if self.repo in self.data["commander"].blacklist:
            self.data["commander"].blacklist.remove(self.repo)
