/** Static export: FastAPI serves dashboard/out on the same origin as /api. */
export default {
  output: "export",
  trailingSlash: false,
  images: { unoptimized: true },
};
