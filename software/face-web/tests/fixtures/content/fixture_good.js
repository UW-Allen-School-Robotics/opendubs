// A content code skin, as one in the private content repository would be: the same exports a
// built-in has. Deliberately draws ONE recognisable primitive so a test can tell it apart from the
// default eyes without judging how it looks.
export const name = "fixture_good";
export function draw(ctx, e) {
  ctx.fillStyle = "#123456";
  ctx.fillRect(e.cx - 5, e.cy - 5, 10, 10);
}
