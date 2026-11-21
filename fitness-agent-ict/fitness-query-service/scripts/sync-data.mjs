import { readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { muscles } from "../../fitness-agent-web/src/data/muscles.ts";
import { exercises } from "../../fitness-agent-web/src/data/exercises.ts";
import { demonstrations } from "../../fitness-agent-web/src/data/demonstrations.ts";

const destination = new URL(
  "../src/main/resources/catalog.json",
  import.meta.url,
);
const catalog = {
  contractVersion: "personal-v1",
  muscles,
  exercises,
  demonstrations: demonstrations.map(({ media, ...record }) => ({
    ...record,
    media: media
      ? {
          kind: media.kind,
          assetKey: "playback-sample",
          description: media.description,
        }
      : null,
  })),
};
const expected = JSON.stringify(catalog, null, 2) + "\n";
if (process.argv.includes("--check")) {
  if ((await readFile(destination, "utf8")) !== expected) {
    throw new Error(
      "Catalog differs from frontend data. Run node scripts/sync-data.mjs, then build.cmd.",
    );
  }
  console.log(
    "Catalog matches frontend: 11 muscles / 21 exercises / 3 demonstration records.",
  );
} else {
  await mkdir(new URL("../src/main/resources/", import.meta.url), {
    recursive: true,
  });
  await writeFile(destination, expected, "utf8");
  console.log(`Synced local metadata: ${fileURLToPath(destination)}`);
}
