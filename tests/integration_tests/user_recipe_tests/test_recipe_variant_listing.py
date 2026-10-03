from collections.abc import Generator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import NoResultFound

from mealie.schema.recipe.recipe import Recipe, RecipeTool
from mealie.schema.recipe.recipe_settings import RecipeSettings
from mealie.schema.recipe.recipe_tool import RecipeToolSave
from tests.utils import api_routes
from tests.utils.fixture_schemas import TestUser


@dataclass
class VariantRecipes:
    original: Recipe
    slow_cooker: Recipe
    air_fryer: Recipe
    standalone: Recipe

    @property
    def all(self) -> tuple[Recipe, ...]:
        return self.original, self.slow_cooker, self.air_fryer, self.standalone


def create_recipe(
    user: TestUser,
    name: str,
    *,
    variant_group_id: UUID | None = None,
    cooking_method: str | None = None,
    created_day: int = 1,
) -> Recipe:
    return user.repos.recipes.create(
        Recipe(
            user_id=user.user_id,
            group_id=user.group_id,
            name=name,
            slug=str(uuid4()),
            variant_group_id=variant_group_id,
            cooking_method=cooking_method,
            created_at=datetime(2026, 1, created_day, tzinfo=UTC),
            settings=RecipeSettings(public=True),
        )
    )


@pytest.fixture
def variant_recipes(unique_user: TestUser) -> Generator[VariantRecipes]:
    # The original is deliberately newer than its variants: root preference must not
    # depend on creation order, including after importing a backup.
    original = create_recipe(unique_user, "Roast lamb", created_day=3)
    assert original.id
    recipes = VariantRecipes(
        original=original,
        slow_cooker=create_recipe(
            unique_user,
            "Slow cooker lamb",
            variant_group_id=original.id,
            cooking_method="Slow Cooker",
            created_day=1,
        ),
        air_fryer=create_recipe(
            unique_user,
            "Air fryer lamb",
            variant_group_id=original.id,
            cooking_method="Air Fryer",
            created_day=2,
        ),
        # Unrelated dishes with the same name must remain separate recipes.
        standalone=create_recipe(unique_user, "Roast lamb", created_day=4),
    )
    yield recipes
    for recipe in recipes.all:
        with suppress(NoResultFound):
            unique_user.repos.recipes.delete(recipe.slug)


def public_recipe_url(user: TestUser) -> str:
    group = user.repos.groups.get_one(user.group_id)
    assert group and group.preferences
    group.preferences.private_group = False
    user.repos.group_preferences.update(group.id, group.preferences)

    household = user.repos.households.get_one(user.household_id)
    assert household and household.preferences
    household.preferences.private_household = False
    user.repos.household_preferences.update(household.id, household.preferences)
    return api_routes.explore_groups_group_slug_recipes(group.slug)


@pytest.mark.parametrize("public", [False, True])
def test_variant_grouping_is_opt_in_and_precedes_pagination(
    api_client: TestClient,
    unique_user: TestUser,
    variant_recipes: VariantRecipes,
    public: bool,
) -> None:
    url = public_recipe_url(unique_user) if public else api_routes.recipes
    headers = {} if public else unique_user.token

    for grouping in ({}, {"groupVariants": False}):
        response = api_client.get(url, headers=headers, params=grouping | {"perPage": -1})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 4
        assert {recipe["id"] for recipe in data["items"]} == {str(recipe.id) for recipe in variant_recipes.all}

    params = {"groupVariants": True, "perPage": 1, "orderBy": "createdAt", "orderDirection": "asc"}
    response = api_client.get(url, headers=headers, params=params)
    assert response.status_code == 200
    first_page = response.json()
    assert first_page["total"] == 2
    assert first_page["total_pages"] == 2
    assert [recipe["id"] for recipe in first_page["items"]] == [str(variant_recipes.original.id)]
    assert first_page["previous"] is None
    assert first_page["next"]

    # Follow the server's guide to ensure grouping survives subsequent pages.
    response = api_client.get(first_page["next"], headers=headers)
    assert response.status_code == 200
    second_page = response.json()
    assert second_page["total"] == 2
    assert second_page["total_pages"] == 2
    assert [recipe["id"] for recipe in second_page["items"]] == [str(variant_recipes.standalone.id)]
    assert second_page["next"] is None
    assert second_page["previous"]


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("filter_type", ["search", "tools", "queryFilter"])
def test_grouped_listing_retains_a_variant_that_matches_the_filter(
    api_client: TestClient,
    unique_user: TestUser,
    variant_recipes: VariantRecipes,
    public: bool,
    filter_type: str,
) -> None:
    url = public_recipe_url(unique_user) if public else api_routes.recipes
    headers = {} if public else unique_user.token
    slow_cooker = variant_recipes.slow_cooker
    tool = None
    if filter_type == "search":
        filter_params = {"search": "Slow cooker"}
    elif filter_type == "tools":
        tool = unique_user.repos.tools.create(RecipeToolSave(name=str(uuid4()), group_id=unique_user.group_id))
        slow_cooker.tools = [RecipeTool.model_validate(tool)]
        unique_user.repos.recipes.update(slow_cooker.slug, slow_cooker)
        filter_params = {"tools": str(tool.id)}
    else:
        filter_params = {"queryFilter": 'cookingMethod = "Slow Cooker"'}

    try:
        response = api_client.get(url, headers=headers, params=filter_params | {"groupVariants": True})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert [recipe["id"] for recipe in data["items"]] == [str(slow_cooker.id)]
    finally:
        if tool:
            unique_user.repos.tools.delete(tool.id)


def test_grouped_favorites_include_a_favorited_variant(
    api_client: TestClient, unique_user: TestUser, variant_recipes: VariantRecipes
) -> None:
    slow_cooker = variant_recipes.slow_cooker
    response = api_client.post(
        api_routes.users_id_favorites_slug(unique_user.user_id, slow_cooker.slug), headers=unique_user.token
    )
    assert response.status_code == 200

    response = api_client.get(
        api_routes.recipes,
        headers=unique_user.token,
        params={"groupVariants": True, "queryFilter": f'favoritedBy.id = "{unique_user.user_id}"'},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [recipe["id"] for recipe in response.json()["items"]] == [str(slow_cooker.id)]


@pytest.mark.parametrize("public", [False, True])
def test_grouped_listing_uses_oldest_remaining_variant_after_root_deletion(
    api_client: TestClient, unique_user: TestUser, variant_recipes: VariantRecipes, public: bool
) -> None:
    url = public_recipe_url(unique_user) if public else api_routes.recipes
    headers = {} if public else unique_user.token
    response = api_client.delete(api_routes.recipes_slug(variant_recipes.original.slug), headers=unique_user.token)
    assert response.status_code == 200

    for direction in ("asc", "desc"):
        response = api_client.get(
            url,
            headers=headers,
            params={"groupVariants": True, "orderBy": "name", "orderDirection": direction},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert {recipe["id"] for recipe in data["items"]} == {
            str(variant_recipes.slow_cooker.id),
            str(variant_recipes.standalone.id),
        }


def test_grouping_does_not_change_the_method_selector_or_direct_recipe_access(
    api_client: TestClient, unique_user: TestUser, variant_recipes: VariantRecipes
) -> None:
    response = api_client.get(
        api_routes.recipes_slug_variants(variant_recipes.slow_cooker.slug), headers=unique_user.token
    )
    assert response.status_code == 200
    assert {recipe["id"] for recipe in response.json()} == {
        str(variant_recipes.original.id),
        str(variant_recipes.slow_cooker.id),
        str(variant_recipes.air_fryer.id),
    }
    for recipe in variant_recipes.all:
        response = api_client.get(api_routes.recipes_slug(recipe.slug), headers=unique_user.token)
        assert response.status_code == 200
        assert response.json()["id"] == str(recipe.id)


def test_public_grouping_selects_only_an_eligible_recipe_in_the_requested_group(
    api_client: TestClient,
    unique_user: TestUser,
    h2_user: TestUser,
    g2_user: TestUser,
    variant_recipes: VariantRecipes,
) -> None:
    url = public_recipe_url(unique_user)
    public_recipe_url(g2_user)
    original = variant_recipes.original
    assert original.id and original.settings
    original.settings.public = False
    unique_user.repos.recipes.update(original.slug, original)

    household = h2_user.repos.households.get_one(h2_user.household_id)
    assert household and household.preferences
    household.preferences.private_household = True
    h2_user.repos.household_preferences.update(household.id, household.preferences)

    # Older records outside the public/group scope must neither be exposed nor
    # suppress the visible member of the family when representatives are ranked.
    inaccessible: list[tuple[TestUser, Recipe]] = []
    for user in (h2_user, g2_user):
        recipe = create_recipe(user, "Hidden lamb", variant_group_id=original.id, cooking_method="Other")
        recipe.created_at = datetime(2025, 1, 1, tzinfo=UTC)
        user.repos.recipes.update(recipe.slug, recipe)
        inaccessible.append((user, recipe))

    try:
        response = api_client.get(url, params={"groupVariants": True})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert {recipe["id"] for recipe in data["items"]} == {
            str(variant_recipes.slow_cooker.id),
            str(variant_recipes.standalone.id),
        }

        response = api_client.get(url, params={"groupVariants": True, "queryFilter": "settings.public = FALSE"})
        assert response.status_code == 200
        assert response.json()["total"] == 0
        assert response.json()["items"] == []
    finally:
        for user, recipe in inaccessible:
            user.repos.recipes.delete(recipe.slug)
