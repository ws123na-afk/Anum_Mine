/**
 * The night glow pass of the renderer (`hqThreeRenderer.ts`): an effect composer with a render pass, an unreal bloom pass and the output pass.
 * A lazy chunk of its own, so the renderer path that every tier downloads does not carry the post-processing code; the renderer starts
 * loading it when its tier's top rung has bloom (or on the first frame that wants it) and draws the plain way until it has arrived.
 */
import { HalfFloatType, Vector2, WebGLRenderTarget, type Camera, type Scene, type WebGLRenderer } from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

export type BloomComposer = Readonly<{ composer: EffectComposer; bloom: UnrealBloomPass }>;

/** Builds the composer at the canvas size in CSS pixels and the device pixel ratio (the same settings the renderer used inline before). */
export function makeBloomComposer(renderer: WebGLRenderer, scene: Scene, camera: Camera, width: number, height: number, dpr: number): BloomComposer {
  const target = new WebGLRenderTarget(Math.round(width * dpr), Math.round(height * dpr), { type: HalfFloatType, samples: 4 });
  const composer = new EffectComposer(renderer, target);
  composer.setPixelRatio(dpr);
  composer.setSize(Math.round(width), Math.round(height));
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new Vector2(Math.round(width), Math.round(height)), 0.85, 0.5, 1.0);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());
  return { composer, bloom };
}
