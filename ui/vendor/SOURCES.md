# Vendored files (downloaded 2026-09-26, with Ali's OK)

| File | Source | Licence |
|---|---|---|
| ort.wasm.min.js, ort-wasm-simd-threaded.wasm, ort-wasm-simd-threaded.mjs | onnxruntime-web 1.20.1, https://cdn.jsdelivr.net/npm/onnxruntime-web@1.20.1/dist/ | MIT |
| melspectrogram.onnx, embedding_model.onnx | openWakeWord v0.5.1 release, https://github.com/dscripka/openWakeWord/releases/tag/v0.5.1 | Apache-2.0 |
| hey_jarvis_v0.1.onnx | same release | CC BY-NC-SA 4.0 (non-commercial; personal use only) |
| three/three.module.js, three/three.core.js | three.js r186 (npm three@0.186.1), jsDelivr's minified builds: https://cdn.jsdelivr.net/npm/three@0.186.1/build/three.module.min.js and three.core.min.js, saved under the unminified names because the module imports ./three.core.js (downloaded 2026-09-27). Minified because the full-size files stall on this PC (a security scanner holds back the end of large JS responses) | MIT (three/LICENSE) |
| fonts/space-grotesk.woff2, fonts/jetbrains-mono.woff2, fonts/sora.woff2 | Fontsource variable builds, latin subset, @fontsource-variable/{space-grotesk,jetbrains-mono,sora}@5.3.0 via https://cdn.jsdelivr.net/npm/ (downloaded 2026-09-27). Served locally so the page never calls Google | SIL OFL 1.1 (fonts/*.LICENSE) |
| three/addons/postprocessing/{EffectComposer,RenderPass,ShaderPass,UnrealBloomPass,OutputPass,MaskPass,Pass}.js, three/addons/shaders/{CopyShader,LuminosityHighPassShader,OutputShader}.js | three.js r186 examples/jsm, https://cdn.jsdelivr.net/npm/three@0.186.1/examples/jsm/ (downloaded 2026-09-27). Bloom, chromatic aberration and vignette for the 3D scene | MIT (three/LICENSE) |

Checksums in SHA256SUMS. Everything here runs locally in the browser; no audio leaves the machine for wake-word detection.
