import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import { sentryVitePlugin } from "@sentry/vite-plugin";
import fs from 'fs';

// 读取 package.json 获取版本号
const packageJson = JSON.parse(fs.readFileSync('./package.json', 'utf-8'));
const version = packageJson.version;
const appName = packageJson.name;
const releaseName = `${appName}@${version}`;

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // 加载环境变量 (.env, .env.local 等)
  const env = loadEnv(mode, process.cwd(), '');

  return {
    plugins: [
      react(),
      tailwindcss(),
      sentryVitePlugin({
        org: env.SENTRY_ORG,
        project: env.SENTRY_PROJECT,
        authToken: env.SENTRY_AUTH_TOKEN,
        // 显式指定 release，确保与前端初始化一致
        release: {
          name: releaseName,
        }
      }),
    ],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    build: {
      sourcemap: true,
      rollupOptions: {
        output: {
          manualChunks: {
            'react-vendor': ['react', 'react-dom', 'react-router'],
            'antd': ['antd'],
            'lodash': ['lodash-es'],
          },
        },
      },
    },
    define: {
      // 注入版本号到前端代码
      __APP_VERSION__: JSON.stringify(version),
      __APP_NAME__: JSON.stringify(appName),
    }
  }
})
