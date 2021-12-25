"""Repository."""
# pylint: disable=broad-except, no-member
from datetime import datetime
import json
import os
import shutil
import tempfile
from typing import List, Optional
import zipfile

from aiogithubapi import AIOGitHubAPIException
import attr
from homeassistant.helpers.json import JSONEncoder

from custom_components.hacs.backup import Backup, BackupNetDaemon
from custom_components.hacs.exceptions import (
    HacsException,
    HacsNotModifiedException,
    HacsRepositoryExistException,
)
from custom_components.hacs.share import get_hacs
from custom_components.hacs.utils.download import async_download_file, download_content
from custom_components.hacs.utils.information import get_info_md_content, get_repository
from custom_components.hacs.utils.logger import getLogger
from custom_components.hacs.utils.path import is_safe
from custom_components.hacs.utils.queue_manager import QueueManager
from custom_components.hacs.utils.store import async_remove_store
from custom_components.hacs.utils.validate import Validate
from custom_components.hacs.utils.validate_repository import (
    common_update_data,
    common_validate,
)
from custom_components.hacs.utils.version import (
    version_left_higher_or_equal_then_right,
    version_left_higher_then_right,
    version_to_download,
)
from custom_components.hacs.validate import async_run_repository_checks


@attr.s(auto_attribs=True)
class RepositoryData:
    """RepositoryData class."""

    archived: bool = False
    authors: List[str] = []
    category: str = ""
    content_in_root: bool = False
    country: List[str] = []
    config_flow: bool = False
    default_branch: str = None
    description: str = ""
    domain: str = ""
    domains: List[str] = []
    downloads: int = 0
    etag_repository: str = None
    file_name: str = ""
    filename: str = ""
    first_install: bool = False
    fork: bool = False
    full_name: str = ""
    hacs: str = None  # Minimum HACS version
    hide: bool = False
    hide_default_branch: bool = False
    homeassistant: str = None  # Minimum Home Assistant version
    id: int = 0
    iot_class: str = None
    installed: bool = False
    installed_commit: str = None
    installed_version: str = None
    open_issues: int = 0
    last_commit: str = None
    last_version: str = None
    last_updated: str = 0
    manifest_name: str = None
    new: bool = True
    persistent_directory: str = None
    pushed_at: str = ""
    releases: bool = False
    render_readme: bool = False
    published_tags: List[str] = []
    selected_tag: str = None
    show_beta: bool = False
    stargazers_count: int = 0
    topics: List[str] = []
    zip_release: bool = False
    _storage_data: Optional[dict] = None

    @property
    def stars(self):
        """Return the stargazers count."""
        return self.stargazers_count or 0

    @property
    def name(self):
        """Return the name."""
        if self.category in ["integration", "netdaemon"]:
            return self.domain
        return self.full_name.split("/")[-1]

    def to_json(self):
        """Export to json."""
        return attr.asdict(self, filter=lambda attr, _: attr.name != "_storage_data")

    def memorize_storage(self, data) -> None:
        """Memorize the storage data."""
        self._storage_data = data

    def export_data(self) -> Optional[dict]:
        """Export to json if the data has changed.

        Returns the data to export if the data needs
        to be written.

        Returns None if the data has not changed.
        """
        export = json.loads(json.dumps(self.to_json(), cls=JSONEncoder))
        return None if self._storage_data == export else export

    @staticmethod
    def create_from_dict(source: dict):
        """Set attributes from dicts."""
        data = RepositoryData()
        for key in source:
            if key not in data.__dict__:
                continue
            if key == "pushed_at":
                if source[key] == "":
                    continue
                if "Z" in source[key]:
                    setattr(
                        data,
                        key,
                        datetime.strptime(source[key], "%Y-%m-%dT%H:%M:%SZ"),
                    )
                else:
                    setattr(
                        data,
                        key,
                        datetime.strptime(source[key], "%Y-%m-%dT%H:%M:%S"),
                    )
            elif key == "id":
                setattr(data, key, str(source[key]))
            elif key == "country":
                if isinstance(source[key], str):
                    setattr(data, key, [source[key]])
                else:
                    setattr(data, key, source[key])
            else:
                setattr(data, key, source[key])
        return data

    def update_data(self, data: dict):
        """Update data of the repository."""
        for key in data:
            if key not in self.__dict__:
                continue
            if key == "pushed_at":
                if data[key] == "":
                    continue
                if "Z" in data[key]:
                    setattr(
                        self,
                        key,
                        datetime.strptime(data[key], "%Y-%m-%dT%H:%M:%SZ"),
                    )
                else:
                    setattr(self, key, datetime.strptime(data[key], "%Y-%m-%dT%H:%M:%S"))
            elif key == "id":
                setattr(self, key, str(data[key]))
            elif key == "country":
                if isinstance(data[key], str):
                    setattr(self, key, [data[key]])
                else:
                    setattr(self, key, data[key])
            else:
                setattr(self, key, data[key])


@attr.s(auto_attribs=True)
class HacsManifest:
    """HacsManifest class."""

    name: str = None
    content_in_root: bool = False
    zip_release: bool = False
    filename: str = None
    manifest: dict = {}
    hacs: str = None
    hide_default_branch: bool = False
    domains: List[str] = []
    country: List[str] = []
    homeassistant: str = None
    persistent_directory: str = None
    iot_class: str = None
    render_readme: bool = False

    @staticmethod
    def from_dict(manifest: dict):
        """Set attributes from dicts."""
        if manifest is None:
            raise HacsException("Missing manifest data")

        manifest_data = HacsManifest()

        manifest_data.manifest = manifest

        if country := manifest.get("country"):
            if isinstance(country, str):
                manifest["country"] = [country]

        for key in manifest:
            setattr(manifest_data, key, manifest[key])
        return manifest_data


class RepositoryVersions:
    """Versions."""

    available = None
    available_commit = None
    installed = None
    installed_commit = None


class RepositoryStatus:
    """Repository status."""

    hide = False
    installed = False
    last_updated = None
    new = True
    selected_tag = None
    show_beta = False
    track = True
    updated_info = False
    first_install = True


class RepositoryInformation:
    """RepositoryInformation."""

    additional_info = None
    authors = []
    category = None
    default_branch = None
    description = ""
    state = None
    full_name = None
    full_name_lower = None
    file_name = None
    javascript_type = None
    homeassistant_version = None
    last_updated = None
    uid = None
    stars = 0
    info = None
    name = None
    topics = []


class RepositoryReleases:
    """RepositoyReleases."""

    last_release = None
    last_release_object = None
    last_release_object_downloads = None
    published_tags = []
    objects = []
    releases = False
    downloads = None


class RepositoryPath:
    """RepositoryPath."""

    local = None
    remote = None


class RepositoryContent:
    """RepositoryContent."""

    path = None
    files = []
    objects = []
    single = False


class HacsRepository:
    """HacsRepository."""

    def __init__(self):
        """Set up HacsRepository."""
        self.hacs = get_hacs()
        self.data = RepositoryData()
        self.content = RepositoryContent()
        self.content.path = RepositoryPath()
        self.information = RepositoryInformation()
        self.repository_object = None
        self.status = RepositoryStatus()
        self.state = None
        self.force_branch = False
        self.integration_manifest = {}
        self.repository_manifest = HacsManifest.from_dict({})
        self.validate = Validate()
        self.releases = RepositoryReleases()
        self.versions = RepositoryVersions()
        self.pending_restart = False
        self.tree = []
        self.treefiles = []
        self.ref = None
        self.logger = getLogger()

    def __str__(self) -> str:
        """Return a string representation of the repository."""
        return f"<{self.data.category.title()} {self.data.full_name}>"

    @property
    def display_name(self):
        """Return display name."""
        if self.repository_manifest.name is not None:
            return self.repository_manifest.name

        if self.data.category == "integration":
            if self.integration_manifest:
                if "name" in self.integration_manifest:
                    return self.integration_manifest["name"]

        return self.data.full_name.split("/")[-1].replace("-", " ").replace("_", " ").title()

    @property
    def ignored_by_country_configuration(self) -> bool:
        """Return True if hidden by country."""
        if self.data.installed:
            return False
        configuration = self.hacs.configuration.country.lower()
        manifest = [entry.lower() for entry in self.repository_manifest.country or []]
        if configuration == "all":
            return False
        if not manifest:
            return False
        return configuration not in manifest

    @property
    def display_status(self):
        """Return display_status."""
        if self.data.new:
            status = "new"
        elif self.pending_restart:
            status = "pending-restart"
        elif self.pending_update:
            status = "pending-upgrade"
        elif self.data.installed:
            status = "installed"
        else:
            status = "default"
        return status

    @property
    def display_status_description(self):
        """Return display_status_description."""
        description = {
            "default": "Not installed.",
            "pending-restart": "Restart pending.",
            "pending-upgrade": "Upgrade pending.",
            "installed": "No action required.",
            "new": "This is a newly added repository.",
        }
        return description[self.display_status]

    @property
    def display_installed_version(self):
        """Return display_authors"""
        if self.data.installed_version is not None:
            installed = self.data.installed_version
        else:
            if self.data.installed_commit is not None:
                installed = self.data.installed_commit
            else:
                installed = ""
        return str(installed)

    @property
    def display_available_version(self):
        """Return display_authors"""
        if self.data.last_version is not None:
            available = self.data.last_version
        else:
            if self.data.last_commit is not None:
                available = self.data.last_commit
            else:
                available = ""
        return str(available)

    @property
    def display_version_or_commit(self):
        """Does the repositoriy use releases or commits?"""
        if self.data.releases:
            version_or_commit = "version"
        else:
            version_or_commit = "commit"
        return version_or_commit

    @property
    def main_action(self):
        """Return the main action."""
        actions = {
            "new": "INSTALL",
            "default": "INSTALL",
            "installed": "REINSTALL",
            "pending-restart": "REINSTALL",
            "pending-upgrade": "UPGRADE",
        }
        return actions[self.display_status]

    @property
    def pending_update(self) -> bool:
        """Return True if pending update."""
        if not self.can_download:
            return False
        if self.data.installed:
            if self.data.selected_tag is not None:
                if self.data.selected_tag == self.data.default_branch:
                    if self.data.installed_commit != self.data.last_commit:
                        return True
                    return False
            if self.display_version_or_commit == "version":
                if version_left_higher_then_right(
                    self.display_available_version,
                    self.display_installed_version,
                ):
                    return True
            if self.display_installed_version != self.display_available_version:
                return True

        return False

    @property
    def can_download(self) -> bool:
        """Return True if we can download."""
        if self.data.homeassistant is not None:
            if self.data.releases:
                if not version_left_higher_or_equal_then_right(
                    self.hacs.core.ha_version.string,
                    self.data.homeassistant,
                ):
                    return False
        return True

    async def common_validate(self, ignore_issues=False):
        """Common validation steps of the repository."""
        await common_validate(self, ignore_issues)

    async def common_registration(self):
        """Common registration steps of the repository."""
        # Attach repository
        if self.repository_object is None:
            try:
                self.repository_object, etag = await get_repository(
                    self.hacs.session,
                    self.hacs.configuration.token,
                    self.data.full_name,
                    None if self.data.installed else self.data.etag_repository,
                )
                self.data.update_data(self.repository_object.attributes)
                self.data.etag_repository = etag
            except HacsNotModifiedException:
                self.logger.debug(
                    "Did not update %s, content was not modified", self.data.full_name
                )
                return

        # Set topics
        self.data.topics = self.data.topics

        # Set stargazers_count
        self.data.stargazers_count = self.data.stargazers_count

        # Set description
        self.data.description = self.data.description

        if self.hacs.system.action:
            if self.data.description is None or len(self.data.description) == 0:
                raise HacsException("::error:: Missing repository description")

    async def common_update(self, ignore_issues=False, force=False):
        """Common information update steps of the repository."""
        self.logger.debug("%s Getting repository information", self)

        # Attach repository
        current_etag = self.data.etag_repository
        try:
            await common_update_data(self, ignore_issues, force)
        except HacsRepositoryExistException:
            self.data.full_name = self.hacs.common.renamed_repositories[self.data.full_name]
            await common_update_data(self, ignore_issues, force)

        if not self.data.installed and (current_etag == self.data.etag_repository) and not force:
            self.logger.debug("Did not update %s, content was not modified", self.data.full_name)
            return False

        # Update last updated
        self.data.last_updated = self.repository_object.attributes.get("pushed_at", 0)

        # Update last available commit
        await self.repository_object.set_last_commit()
        self.data.last_commit = self.repository_object.last_commit

        # Get the content of hacs.json
        await self.get_repository_manifest_content()

        # Update "info.md"
        self.information.additional_info = await get_info_md_content(self)

        return True

    async def download_zip_files(self, validate):
        """Download ZIP archive from repository release."""
        download_queue = QueueManager()
        try:
            contents = False

            for release in self.releases.objects:
                self.logger.info("%s ref: %s ---  tag: %s.", self, self.ref, release.tag_name)
                if release.tag_name == self.ref.split("/")[1]:
                    contents = release.assets

            if not contents:
                return validate

            for content in contents or []:
                download_queue.add(self.async_download_zip_file(content, validate))

            await download_queue.execute()
        except (Exception, BaseException):
            validate.errors.append("Download was not completed")

        return validate

    async def async_download_zip_file(self, content, validate):
        """Download ZIP archive from repository release."""
        try:
            filecontent = await async_download_file(content.download_url)

            if filecontent is None:
                validate.errors.append(f"[{content.name}] was not downloaded")
                return

            temp_dir = await self.hacs.hass.async_add_executor_job(tempfile.mkdtemp)
            temp_file = f"{temp_dir}/{self.data.filename}"

            result = await self.hacs.async_save_file(temp_file, filecontent)
            with zipfile.ZipFile(temp_file, "r") as zip_file:
                zip_file.extractall(self.content.path.local)

            def cleanup_temp_dir():
                """Cleanup temp_dir."""
                if os.path.exists(temp_dir):
                    self.logger.debug("Cleaning up %s", temp_dir)
                    shutil.rmtree(temp_dir)

            if result:
                self.logger.info("%s Download of %s completed", self, content.name)
                await self.hacs.hass.async_add_executor_job(cleanup_temp_dir)
                return

            validate.errors.append(f"[{content.name}] was not downloaded")
        except (Exception, BaseException):
            validate.errors.append("Download was not completed")

        return validate

    async def download_content(self, validate, _directory_path, _local_directory, _ref):
        """Download the content of a directory."""

        validate = await download_content(self)
        return validate

    async def get_repository_manifest_content(self):
        """Get the content of the hacs.json file."""
        if not "hacs.json" in [x.filename for x in self.tree]:
            if self.hacs.system.action:
                raise HacsException("::error:: No hacs.json file in the root of the repository.")
            return
        if self.hacs.system.action:
            self.logger.info("%s Found hacs.json", self)

        self.ref = version_to_download(self)

        try:
            manifest = await self.repository_object.get_contents("hacs.json", self.ref)
            self.repository_manifest = HacsManifest.from_dict(json.loads(manifest.content))
            self.data.update_data(json.loads(manifest.content))
        except (AIOGitHubAPIException, Exception) as exception:  # Gotta Catch 'Em All
            if self.hacs.system.action:
                raise HacsException(
                    f"::error:: hacs.json file is not valid ({exception})."
                ) from None
        if self.hacs.system.action:
            self.logger.info("%s hacs.json is valid", self)

    def remove(self):
        """Run remove tasks."""
        self.logger.info("%s Starting removal", self)

        if self.hacs.repositories.is_registered(repository_id=str(self.data.id)):
            self.hacs.repositories.unregister(self)

    async def uninstall(self):
        """Run uninstall tasks."""
        self.logger.info("%s Uninstalling", self)
        if not await self.remove_local_directory():
            raise HacsException("Could not uninstall")
        self.data.installed = False
        if self.data.category == "integration":
            if self.data.config_flow:
                await self.reload_custom_components()
            else:
                self.pending_restart = True
        elif self.data.category == "theme":
            try:
                await self.hacs.hass.services.async_call("frontend", "reload_themes", {})
            except (Exception, BaseException):  # pylint: disable=broad-except
                pass

        await async_remove_store(self.hacs.hass, f"hacs/{self.data.id}.hacs")

        self.data.installed_version = None
        self.data.installed_commit = None
        self.hacs.hass.bus.async_fire(
            "hacs/repository",
            {"id": 1337, "action": "uninstall", "repository": self.data.full_name},
        )

    async def remove_local_directory(self):
        """Check the local directory."""
        from asyncio import sleep

        try:
            if self.data.category == "python_script":
                local_path = f"{self.content.path.local}/{self.data.name}.py"
            elif self.data.category == "theme":
                if os.path.exists(
                    f"{self.hacs.core.config_path}/{self.hacs.configuration.theme_path}/{self.data.name}.yaml"
                ):
                    os.remove(
                        f"{self.hacs.core.config_path}/{self.hacs.configuration.theme_path}/{self.data.name}.yaml"
                    )
                local_path = self.content.path.local
            elif self.data.category == "integration":
                if not self.data.domain:
                    self.logger.error("%s Missing domain", self)
                    return False
                local_path = self.content.path.local
            else:
                local_path = self.content.path.local

            if os.path.exists(local_path):
                if not is_safe(self.hacs, local_path):
                    self.logger.error("%s Path %s is blocked from removal", self, local_path)
                    return False
                self.logger.debug("%s Removing %s", self, local_path)

                if self.data.category in ["python_script"]:
                    os.remove(local_path)
                else:
                    shutil.rmtree(local_path)

                while os.path.exists(local_path):
                    await sleep(1)
            else:
                self.logger.debug(
                    "%s Presumed local content path %s does not exist", self, local_path
                )

        except (Exception, BaseException) as exception:
            self.logger.debug("%s Removing %s failed with %s", self, local_path, exception)
            return False
        return True

    async def async_pre_registration(self):
        """Run pre registration steps."""

    async def async_registration(self, ref=None) -> None:
        """Run registration steps."""
        await self.async_pre_registration()

        if ref is not None:
            self.data.selected_tag = ref
            self.ref = ref
            self.force_branch = True

        if not await self.validate_repository():
            return False

        # Run common registration steps.
        await self.common_registration()

        # Set correct local path
        self.content.path.local = self.localpath

        # Run local post registration steps.
        await self.async_post_registration()

    async def async_post_registration(self):
        """Run post registration steps."""
        await async_run_repository_checks(self.hacs, self)

    async def async_pre_install(self) -> None:
        """Run pre install steps."""

    async def _async_pre_install(self) -> None:
        """Run pre install steps."""
        self.logger.info("Running pre installation steps")
        await self.async_pre_install()
        self.logger.info("Pre installation steps completed")

    async def async_install(self) -> None:
        """Run install steps."""
        await self._async_pre_install()
        self.logger.info("Running installation steps")
        await self.async_install_repository()
        self.logger.info("Installation steps completed")
        await self._async_post_install()

    async def async_post_installation(self) -> None:
        """Run post install steps."""

    async def _async_post_install(self) -> None:
        """Run post install steps."""
        self.logger.info("Running post installation steps")
        await self.async_post_installation()
        self.data.new = False
        self.hacs.hass.bus.async_fire(
            "hacs/repository",
            {"id": 1337, "action": "install", "repository": self.data.full_name},
        )
        self.logger.info("Post installation steps completed")

    async def async_install_repository(self):
        """Common installation steps of the repository."""
        hacs = get_hacs()
        persistent_directory = None
        await self.update_repository()
        if self.content.path.local is None:
            raise HacsException("repository.content.path.local is None")
        self.validate.errors.clear()

        if not self.can_download:
            raise HacsException("The version of Home Assistant is not compatible with this version")

        version = version_to_download(self)
        if version == self.data.default_branch:
            self.ref = version
        else:
            self.ref = f"tags/{version}"

        if self.data.installed and self.data.category == "netdaemon":
            persistent_directory = BackupNetDaemon(hacs=hacs, repository=self)
            await hacs.hass.async_add_executor_job(persistent_directory.create)

        elif self.data.persistent_directory:
            if os.path.exists(f"{self.content.path.local}/{self.data.persistent_directory}"):
                persistent_directory = Backup(
                    hacs=hacs,
                    local_path=f"{self.content.path.local}/{self.data.persistent_directory}",
                    backup_path=tempfile.gettempdir() + "/hacs_persistent_directory/",
                )
                await hacs.hass.async_add_executor_job(persistent_directory.create)

        if self.data.installed and not self.content.single:
            backup = Backup(hacs=hacs, local_path=self.content.path.local)
            await hacs.hass.async_add_executor_job(backup.create)

        if self.data.zip_release and version != self.data.default_branch:
            await self.download_zip_files(self.validate)
        else:
            await download_content(self)

        if self.validate.errors:
            for error in self.validate.errors:
                self.logger.error(error)
            if self.data.installed and not self.content.single:
                await hacs.hass.async_add_executor_job(backup.restore)

        if self.data.installed and not self.content.single:
            await hacs.hass.async_add_executor_job(backup.cleanup)

        if persistent_directory is not None:
            await hacs.hass.async_add_executor_job(persistent_directory.restore)
            await hacs.hass.async_add_executor_job(persistent_directory.cleanup)

        if self.validate.success:
            self.data.installed = True
            self.data.installed_commit = self.data.last_commit

            if version == self.data.default_branch:
                self.data.installed_version = None
            else:
                self.data.installed_version = version
