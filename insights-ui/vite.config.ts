import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/postcss';
import {fileURLToPath} from 'node:url';
export default defineConfig({define:{'process.env.NODE_ENV':'"production"'},plugins:[react()],resolve:{alias:{'@':fileURLToPath(new URL('.',import.meta.url))}},css:{postcss:{plugins:[tailwind()]}},build:{minify:true,outDir:'../static/getreep-insights',emptyOutDir:true,lib:{entry:'entry.tsx',formats:['es'],fileName:()=> 'dashboard.js',cssFileName:'dashboard'}}});
