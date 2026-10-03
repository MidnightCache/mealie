import { afterEach, describe, expect, test, vi } from "vitest";
import { effectScope, type EffectScope } from "vue";
import { useRecipeSearch } from "../use-recipe-search";
import type { UserApi } from "~/lib/api";

const scopes: EffectScope[] = [];

afterEach(() => {
  scopes.splice(0).forEach(scope => scope.stop());
});

describe("recipe search method grouping", () => {
  test.each([false, true])("requests grouped methods only when enabled: %s", async (groupVariants) => {
    const recipes = [{ id: "slow-cooker", slug: "slow-cooker-lamb" }];
    const request = vi.fn().mockResolvedValue({ data: { items: recipes } });
    const api = { recipes: { search: request } } as unknown as UserApi;
    const scope = effectScope();
    scopes.push(scope);
    const search = scope.run(() => groupVariants ? useRecipeSearch(api, true) : useRecipeSearch(api))!;

    search.query.value = "slow cooker";
    await search.trigger();

    expect(request).toHaveBeenCalledWith(expect.objectContaining({
      search: "slow cooker",
      groupVariants,
    }));
    expect(search.data.value).toEqual(recipes);
  });
});
