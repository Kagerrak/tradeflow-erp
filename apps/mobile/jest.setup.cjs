const {
  fetch: crossFetch,
  Headers,
  Request,
  Response,
} = require("cross-fetch");

Object.assign(globalThis, {
  fetch: crossFetch,
  Headers,
  Request,
  Response,
});
