"""Blueprint for HacsBase."""
# pylint: disable=too-few-public-methods
import logging
import uuid
from datetime import timedelta

from homeassistant.helpers.event import async_track_time_interval
from custom_components.hacs.aiogithub import AIOGitHubException

_LOGGER = logging.getLogger('custom_components.hacs.hacs')


class HacsBase:
    """The base class of HACS, nested thoughout the project."""
    import custom_components.hacs.const as const

    migration = None
    storage = None
    hacs = None
    data = {"hacs": {}}
    data["task_running"] = True
    hass = None
    config_dir = None
    aiogithub = None
    blacklist = []
    repositories = {}

    url_path = {}
    for endpoint in ["api", "error", "overview", "static", "store", "settings", "repository"]:
        url_path[endpoint] = "/community_{}-{}".format(str(uuid.uuid4()), str(uuid.uuid4()))

    async def startup_tasks(self):
        """Run startup_tasks."""
        from custom_components.hacs.hacsrepositoryintegration import HacsRepositoryIntegration
        self.data["task_running"] = True

        _LOGGER.info("Runing startup tasks.")

        custom_log_level = {"custom_components.hacs": "debug"}
        await self.hass.services.async_call("logger", "set_level", custom_log_level)

        # For installed repositories only.
        async_track_time_interval(self.hass, self.recuring_tasks(installed_only=True), timedelta(minutes=self.const.UPDATE["full"]))

        # For the rest.
        async_track_time_interval(self.hass, self.recuring_tasks, timedelta(minutes=self.const.UPDATE["full"]))

        # Check for updates to HACS.
        repository = await self.aiogithub.get_repo("custom-components/hacs")
        repository = HacsRepositoryIntegration("custom-components/hacs", repository)
        await repository.setup_repository()
        self.repositories[repository.repository_id] = repository

        # Make sure we have the correct version
        self.data["hacs"]["local"] = self.const.VERSION

        _LOGGER.info("Trying to load existing data.")

        # Check if migration is needed, or load existing data.
        await self.migration.validate()

        self.data["task_running"] = False

    async def register_new_repository(self, element_type, repo, repositoryobject=None):
        """Register a new repository."""
        from custom_components.hacs.exceptions import HacsBaseException, HacsRequirement
        from custom_components.hacs.blueprints import HacsRepositoryIntegration, HacsRepositoryPlugin

        _LOGGER.debug("Starting repository registration for %s", repo)

        if element_type == "integration":
            repository = HacsRepositoryIntegration(repo, repositoryobject)
            await repository.set_repository()

        elif element_type == "plugin":
            repository = HacsRepositoryPlugin(repo, repositoryobject)
            await repository.set_repository()

        else:
            return False

        setup_result = True
        try:
            await repository.setup_repository()
        except (HacsRequirement, HacsBaseException, AIOGitHubException) as exception:
            _LOGGER.debug("%s - %s", repository.repository_name, exception)
            setup_result = False

        if setup_result:
            self.repositories[repository.repository_id] = repository

        else:
            if repo not in self.blacklist:
                self.blacklist.append(repo)
            _LOGGER.debug("%s - Could not register.", repo)
        return repository, setup_result

    async def update_repositories(self):
        """Run update on registerd repositories, and register new."""
        self.data["task_running"] = True

        _LOGGER.debug("Skipping repositories in blacklist %s", str(self.blacklist))

        # Running update on registerd repositories
        if self.repositories:
            for repository in self.repositories:
                try:
                    repository = self.repositories[repository]
                    _LOGGER.info("Running update for %s", repository.repository_name)
                    if repository.track or repository.repository_name in self.blacklist:
                        continue
                    await repository.update()
                except AIOGitHubException as exception:
                    _LOGGER.debug("%s - %s", repository.repository_name, exception)

        # Register new repositories
        integrations, plugins = await self.get_repositories()

        repository_types = {"integration": integrations, "plugin": plugins}

        for repository_type in repository_types:
            for repository in repository_types[repository_type]:
                if repository.archived:
                    continue
                elif repository.full_name in self.blacklist:
                    continue
                elif str(repository.id) in self.repositories:
                    continue
                else:
                    try:
                        await self.register_new_repository(repository_type, repository.full_name, repository)
                    except AIOGitHubException as exception:
                        _LOGGER.debug("%s - %s", repository.full_name, exception)
        await self.storage.set()
        self.data["task_running"] = False

    async def get_repositories(self):
        """Get defined repositories."""
        repositories = {}

        # Get org repositories
        repositories["integration"] = await self.aiogithub.get_org_repos("custom-components")
        repositories["plugin"] = await self.aiogithub.get_org_repos("custom-cards")

        # Additional repositories (Not implemented)
        for repository_type in self.const.DEFAULT_REPOSITORIES:
            for repository in self.const.DEFAULT_REPOSITORIES[repository_type]:
                result = await self.aiogithub.get_repo(repository)
                repositories[repository_type].append(result)

        return repositories["integration"], repositories["plugin"]

    async def recuring_tasks(self, installed_only=True):
        """Recuring tasks."""

        if installed_only:
            if self.repositories:
                for repository in self.repositories:
                    try:
                        repository = self.repositories[repository]
                        _LOGGER.info("Running update for %s", repository.repository_name)
                        if repository.track or repository.repository_name in self.blacklist:
                            continue
                        if not repository.installed:
                            continue
                        await repository.update()
                    except AIOGitHubException as exception:
                        _LOGGER.debug("%s - %s", repository.repository_name, exception)
            return

        # Update everyting if installed_only=False
        await self.update_repositories()
