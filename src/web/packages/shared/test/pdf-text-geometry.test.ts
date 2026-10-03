import assert from "node:assert/strict";
import test from "node:test";

import { pdfTextItemPlacement } from "../src/pdf-text-geometry.ts";


test("maps a reverse-advancing rotated item into its displayed bounds", () => {
  const placement = pdfTextItemPlacement(
    {
      transform: [0, 20.887, -15.022, 0, 756.068, 214.321],
      width: 182.28,
      height: 15.022,
    },
    {
      width: 612,
      height: 792,
      transform: [0, -1, -1, 0, 612, 792],
    },
    { ascent: 1.075, descent: -0.299 },
  );

  assert.ok(placement.inlineEndX < placement.inlineStartX);
  assert.ok(Math.abs(placement.normLeft - 0.3520) < 0.0001);
  assert.ok(Math.abs(placement.normTop - 0.0397) < 0.0001);
  assert.ok(Math.abs(placement.normRight - 0.6498) < 0.0001);
  assert.ok(Math.abs(placement.normBottom - 0.0658) < 0.0001);
});

test("uses font ascent and descent for an ordinary horizontal item", () => {
  const placement = pdfTextItemPlacement(
    {
      transform: [20.887, 0, 0, 15.022, 211.681, 744.412],
      width: 182.28,
      height: 15.022,
    },
    {
      width: 612,
      height: 792,
      transform: [1, 0, 0, -1, 0, 792],
    },
    { ascent: 1.075, descent: -0.299 },
  );

  assert.ok(placement.inlineEndX > placement.inlineStartX);
  assert.ok(Math.abs(placement.normLeft - 0.3459) < 0.0001);
  assert.ok(Math.abs(placement.normTop - 0.0397) < 0.0001);
  assert.ok(Math.abs(placement.normRight - 0.6437) < 0.0001);
  assert.ok(Math.abs(placement.normBottom - 0.0658) < 0.0001);
});
