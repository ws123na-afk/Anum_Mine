/** Reads what the browser says about its graphics (browser only). The pure report and verdict logic is in `lib/hqGpuReport.ts`. */
import { USED_EXTENSIONS, describeAgent, isSoftwareRenderer, type GpuProbe } from '../lib/hqGpuReport.ts';

type AnyGl = WebGL2RenderingContext | WebGLRenderingContext;

function readFrom(gl: AnyGl, version: 1 | 2): GpuProbe {
  const debug = gl.getExtension('WEBGL_debug_renderer_info');
  const unmasked = debug !== null;
  const renderer = String(unmasked ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
  const vendor = String(unmasked ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR));
  const supported = gl.getSupportedExtensions() ?? [];
  const aniso = gl.getExtension('EXT_texture_filter_anisotropic');
  const nav = typeof navigator === 'undefined' ? null : (navigator as Navigator & { deviceMemory?: number });
  const agent = describeAgent(nav?.userAgent ?? '');
  return {
    webglVersion: version, renderer, vendor, unmasked,
    software: unmasked ? isSoftwareRenderer(renderer) : null,
    maxTextureSize: Number(gl.getParameter(gl.MAX_TEXTURE_SIZE)),
    maxCubeMapSize: Number(gl.getParameter(gl.MAX_CUBE_MAP_TEXTURE_SIZE)),
    maxAnisotropy: aniso ? Number(gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT)) : 1,
    maxVertexUniformVectors: Number(gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS)),
    maxFragmentUniformVectors: Number(gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS)),
    extensions: [...supported].sort(),
    used: Object.fromEntries(USED_EXTENSIONS.map((e) => [e, supported.includes(e)])),
    dpr: window.devicePixelRatio || 1,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    cores: typeof nav?.hardwareConcurrency === 'number' ? nav.hardwareConcurrency : null,
    deviceMemoryGb: typeof nav?.deviceMemory === 'number' ? nav.deviceMemory : null,
    browser: agent.browser, os: agent.os,
  };
}

const NONE: GpuProbe = {
  webglVersion: 0, renderer: '', vendor: '', unmasked: false, software: null, maxTextureSize: 0, maxCubeMapSize: 0, maxAnisotropy: 0,
  maxVertexUniformVectors: 0, maxFragmentUniformVectors: 0, extensions: [], used: {}, dpr: 1, viewport: { width: 0, height: 0 }, cores: null, deviceMemoryGb: null, browser: 'unknown', os: 'unknown',
};

/** From the live scene's context when there is one (no second context is made), else from a scratch canvas that is released straight away. */
export function readGpuProbe(live: WebGL2RenderingContext | null): GpuProbe {
  try {
    if (live && !live.isContextLost()) return readFrom(live, 2);
    for (const version of [2, 1] as const) {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext(version === 2 ? 'webgl2' : 'webgl') as AnyGl | null;
      if (!gl) continue;
      try { return readFrom(gl, version); } finally { gl.getExtension('WEBGL_lose_context')?.loseContext(); }
    }
  } catch { /* a locked-down browser: report that nothing could be read */ }
  return { ...NONE, dpr: window.devicePixelRatio || 1, viewport: { width: window.innerWidth, height: window.innerHeight } };
}
