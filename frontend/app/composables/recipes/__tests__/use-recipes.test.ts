import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { useLazyRecipes } from "../use-recipes";

const mocks = vi.hoisted(() => ({
  privateGetAll: vi.fn(),
  publicGetAll: vi.fn(),
}));

vi.mock("~/composables/api", () => ({
  useUserApi: () => ({ recipes: { getAll: mocks.privateGetAll } }),
}));

vi.mock("~/composables/api/api-client", () => ({
  usePublicExploreApi: () => ({ explore: { recipes: { getAll: mocks.publicGetAll } } }),
}));

describe("recipe browsing queries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.privateGetAll.mockResolvedValue({ data: { items: [] } });
    mocks.publicGetAll.mockResolvedValue({ data: { items: [] } });
    vi.stubGlobal("useRouter", () => ({ push: vi.fn() }));
    vi.stubGlobal("useRoute", () => ({ query: {} }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test.each([null, "public-group"])("passes grouped searches and filters to the API for %s", async (groupSlug) => {
    const { fetchMore } = useLazyRecipes(groupSlug);
    const queryFilter = "favoritedBy.id = \"user-id\"";

    await fetchMore(2, 20, "name", "asc", null, {
      search: "slow cooker",
      groupVariants: true,
      tools: ["slow-cooker-id"],
    }, queryFilter);

    const getAll = groupSlug ? mocks.publicGetAll : mocks.privateGetAll;
    expect(getAll).toHaveBeenCalledWith(2, 20, expect.objectContaining({
      search: "slow cooker",
      groupVariants: true,
      tools: ["slow-cooker-id"],
      queryFilter,
    }));
  });

  test("leaves individual methods available to recipe selectors by default", async () => {
    const { fetchMore } = useLazyRecipes();

    await fetchMore(1, 20, "name", "asc", null, { search: "lamb" });

    expect(mocks.privateGetAll).toHaveBeenCalledWith(1, 20, expect.objectContaining({
      search: "lamb",
      groupVariants: undefined,
    }));
  });

  test("groups random navigation with the same filters as browsing", async () => {
    const original = { id: "original", slug: "lamb" };
    mocks.privateGetAll.mockResolvedValue({ data: { items: [original] } });
    const { getRandom } = useLazyRecipes();

    const result = await getRandom({ groupVariants: true, cookbook: "dinners" });

    expect(mocks.privateGetAll).toHaveBeenCalledWith(1, 1, expect.objectContaining({
      orderBy: "random",
      groupVariants: true,
      cookbook: "dinners",
    }));
    expect(result).toEqual(original);
  });
});
