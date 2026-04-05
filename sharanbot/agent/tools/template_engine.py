"""TemplateEngine — modern project scaffolding via real CLI tools.

Wraps real scaffolding CLIs (create-next-app, create-vite, flutter create, etc.)
and falls back to rich built-in templates when offline or CLI unavailable.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


# ── Built-in fallback templates ───────────────────────────────────────

_NEXTJS_TEMPLATE = {
    "package.json": """{
  "name": "{name}",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint"
  },
  "dependencies": {
    "next": "^14.0.0",
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  },
  "devDependencies": {
    "@types/node": "^20",
    "@types/react": "^18",
    "typescript": "^5",
    "tailwindcss": "^3.4",
    "postcss": "^8",
    "autoprefixer": "^10"
  }
}""",
    "tsconfig.json": """{
  "compilerOptions": {
    "target": "es5",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
  "exclude": ["node_modules"]
}""",
    "next.config.js": "/** @type {import('next').NextConfig} */\nconst nextConfig = {}\nmodule.exports = nextConfig\n",
    "tailwind.config.ts": """import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}', './app/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: { extend: {} },
  plugins: [],
}
export default config
""",
    "postcss.config.js": "module.exports = {\n  plugins: {\n    tailwindcss: {},\n    autoprefixer: {},\n  },\n}\n",
    "src/app/layout.tsx": """import './globals.css'
import type {{ Metadata }} from 'next'

export const metadata: Metadata = {{
  title: '{name}',
  description: '{description}',
}}

export default function RootLayout({{ children }}: {{ children: React.ReactNode }}) {{
  return (
    <html lang="en">
      <body>{{children}}</body>
    </html>
  )
}}
""",
    "src/app/page.tsx": """export default function Home() {{
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-24">
      <h1 className="text-4xl font-bold">{name}</h1>
      <p className="mt-4 text-lg text-gray-600">{description}</p>
    </main>
  )
}}
""",
    "src/app/globals.css": "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n",
    ".gitignore": "node_modules/\n.next/\nout/\n.env*.local\n",
}

_VITE_REACT_TEMPLATE = {
    "package.json": """{
  "name": "{name}",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  },
  "devDependencies": {
    "@types/react": "^18.2.0",
    "@types/react-dom": "^18.2.0",
    "@vitejs/plugin-react": "^4.2.0",
    "typescript": "^5.3.0",
    "vite": "^5.0.0",
    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0"
  }
}""",
    "vite.config.ts": """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
})
""",
    "tsconfig.json": """{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}""",
    "index.html": """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{name}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
""",
    "src/main.tsx": """import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
""",
    "src/App.tsx": """function App() {{
  return (
    <div className="min-h-screen flex flex-col items-center justify-center">
      <h1 className="text-4xl font-bold">{name}</h1>
      <p className="mt-4 text-lg text-gray-600">{description}</p>
    </div>
  )
}}

export default App
""",
    "src/index.css": "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n",
    "tailwind.config.js": "/** @type {import('tailwindcss').Config} */\nexport default {\n  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],\n  theme: { extend: {} },\n  plugins: [],\n}\n",
    "postcss.config.js": "export default {\n  plugins: {\n    tailwindcss: {},\n    autoprefixer: {},\n  },\n}\n",
    ".gitignore": "node_modules/\ndist/\n.env*.local\n",
}

_FLUTTER_TEMPLATE = {
    "pubspec.yaml": """name: {name}
description: {description}
version: 0.1.0

environment:
  sdk: '>=3.0.0 <4.0.0'

dependencies:
  flutter:
    sdk: flutter
  cupertino_icons: ^1.0.6

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^3.0.0
""",
    "lib/main.dart": """import 'package:flutter/material.dart';

void main() {{
  runApp(const MyApp());
}}

class MyApp extends StatelessWidget {{
  const MyApp({{super.key}});

  @override
  Widget build(BuildContext context) {{
    return MaterialApp(
      title: '{name}',
      theme: ThemeData(
        colorSchemeSeed: Colors.blue,
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }}
}}

class HomePage extends StatelessWidget {{
  const HomePage({{super.key}});

  @override
  Widget build(BuildContext context) {{
    return Scaffold(
      appBar: AppBar(title: const Text('{name}')),
      body: const Center(
        child: Text('Hello from {name}!', style: TextStyle(fontSize: 24)),
      ),
    );
  }}
}}
""",
    "analysis_options.yaml": "include: package:flutter_lints/flutter.yaml\n",
    ".gitignore": ".dart_tool/\n.packages\nbuild/\n.flutter-plugins\n.flutter-plugins-dependencies\n",
}

_EXPO_TEMPLATE = {
    "package.json": """{
  "name": "{name}",
  "version": "0.1.0",
  "main": "expo-router/entry",
  "scripts": {
    "start": "expo start",
    "android": "expo start --android",
    "ios": "expo start --ios",
    "web": "expo start --web"
  },
  "dependencies": {
    "expo": "~50.0.0",
    "expo-router": "~3.4.0",
    "react": "18.2.0",
    "react-native": "0.73.0",
    "react-native-safe-area-context": "4.8.2",
    "react-native-screens": "~3.29.0"
  },
  "devDependencies": {
    "@babel/core": "^7.20.0",
    "@types/react": "~18.2.0",
    "typescript": "^5.1.0"
  }
}""",
    "app/_layout.tsx": """import { Stack } from 'expo-router';

export default function RootLayout() {
  return <Stack />;
}
""",
    "app/index.tsx": """import {{ Text, View, StyleSheet }} from 'react-native';

export default function Home() {{
  return (
    <View style={styles.container}>
      <Text style={styles.title}>{name}</Text>
      <Text style={styles.subtitle}>{description}</Text>
    </View>
  );
}}

const styles = StyleSheet.create({{
  container: {{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 }},
  title: {{ fontSize: 32, fontWeight: 'bold' }},
  subtitle: {{ fontSize: 16, color: '#666', marginTop: 8 }},
}});
""",
    "tsconfig.json": '{\n  "extends": "expo/tsconfig.base",\n  "compilerOptions": { "strict": true }\n}\n',
    "app.json": """{
  "expo": {
    "name": "{name}",
    "slug": "{name}",
    "version": "0.1.0",
    "orientation": "portrait",
    "scheme": "{name}",
    "platforms": ["ios", "android", "web"]
  }
}""",
    ".gitignore": "node_modules/\n.expo/\ndist/\n",
}

_DJANGO_TEMPLATE = {
    "manage.py": """#!/usr/bin/env python
import os, sys

def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', '{name}.settings')
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
""",
    "{name}/__init__.py": "",
    "{name}/settings.py": """from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = 'change-me-in-production'
DEBUG = True
ALLOWED_HOSTS = ['*']
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth',
    'django.contrib.contenttypes', 'django.contrib.sessions',
    'django.contrib.messages', 'django.contrib.staticfiles',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]
ROOT_URLCONF = '{name}.urls'
TEMPLATES = [{{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {{'context_processors': [
        'django.template.context_processors.debug',
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]}},
}}]
DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}}}
STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
""",
    "{name}/urls.py": """from django.contrib import admin
from django.urls import path
from django.http import JsonResponse

def health(request):
    return JsonResponse({{'status': 'ok'}})

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health),
]
""",
    "{name}/wsgi.py": """import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', '{name}.settings')
application = get_wsgi_application()
""",
    "requirements.txt": "django>=5.0\n",
    ".gitignore": "__pycache__/\n*.py[cod]\ndb.sqlite3\n.env\n*.egg-info/\n",
}

_DOCKER_COMPOSE_FULLSTACK = {
    "docker-compose.yml": """version: '3.8'
services:
  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    volumes:
      - ./frontend:/app
      - /app/node_modules
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:8000

  backend:
    build: ./backend
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/{name}
    depends_on:
      - db

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: {name}
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
""",
    "frontend/Dockerfile": "FROM node:20-alpine\nWORKDIR /app\nCOPY package*.json ./\nRUN npm install\nCOPY . .\nEXPOSE 3000\nCMD [\"npm\", \"run\", \"dev\"]\n",
    "backend/Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install -r requirements.txt\nCOPY . .\nEXPOSE 8000\nCMD [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\", \"--reload\"]\n",
    ".gitignore": "node_modules/\n__pycache__/\n.env\n*.pyc\n",
}


# ── CLI scaffolder commands ───────────────────────────────────────────

_CLI_SCAFFOLDERS: dict[str, dict[str, Any]] = {
    "nextjs": {
        "check": "npx --version",
        "command": 'npx create-next-app@latest "{path}" --typescript --tailwind --eslint --app --src-dir --no-import-alias --use-npm',
        "fallback": _NEXTJS_TEMPLATE,
        "ready_pattern": "Success",
        "timeout": 120,
    },
    "vite-react": {
        "check": "npm --version",
        "command": 'npm create vite@latest "{path}" -- --template react-ts',
        "fallback": _VITE_REACT_TEMPLATE,
        "ready_pattern": "Done",
        "timeout": 60,
    },
    "vite-vue": {
        "check": "npm --version",
        "command": 'npm create vite@latest "{path}" -- --template vue-ts',
        "fallback": _VITE_REACT_TEMPLATE,  # reuse structure, different content
        "timeout": 60,
    },
    "vite-svelte": {
        "check": "npm --version",
        "command": 'npm create vite@latest "{path}" -- --template svelte-ts',
        "fallback": None,
        "timeout": 60,
    },
    "flutter": {
        "check": "flutter --version",
        "command": 'flutter create "{path}"',
        "fallback": _FLUTTER_TEMPLATE,
        "timeout": 120,
    },
    "expo": {
        "check": "npx --version",
        "command": 'npx create-expo-app@latest "{path}" --template tabs',
        "fallback": _EXPO_TEMPLATE,
        "timeout": 120,
    },
    "django": {
        "check": "python -c \"import django\"",
        "command": 'django-admin startproject {name} "{path}"',
        "fallback": _DJANGO_TEMPLATE,
        "timeout": 60,
    },
    "docker-fullstack": {
        "check": "docker --version",
        "command": None,  # always use template
        "fallback": _DOCKER_COMPOSE_FULLSTACK,
        "timeout": 30,
    },
}

# Map aliases to canonical names
_TEMPLATE_ALIASES: dict[str, str] = {
    "next": "nextjs", "next.js": "nextjs", "nextjs": "nextjs",
    "vite": "vite-react", "vite-react": "vite-react", "react-vite": "vite-react",
    "react": "vite-react",
    "vue": "vite-vue", "vite-vue": "vite-vue",
    "svelte": "vite-svelte", "vite-svelte": "vite-svelte",
    "flutter": "flutter",
    "expo": "expo", "react-native": "expo", "react native": "expo", "rn": "expo",
    "django": "django",
    "docker": "docker-fullstack", "docker-compose": "docker-fullstack",
    "fullstack": "docker-fullstack", "full-stack": "docker-fullstack",
    # Original types still supported
    "python": "python", "node": "node", "fastapi": "fastapi",
    "express": "express", "static": "static",
}


class TemplateEngineTool(Tool):
    """Scaffold modern projects using real CLI tools with rich fallback templates."""

    @property
    def name(self) -> str:
        return "scaffold_project"

    @property
    def description(self) -> str:
        return (
            "Scaffold a modern project using real CLI tools (create-next-app, create-vite, "
            "flutter create, create-expo-app, django-admin, etc.). Falls back to rich "
            "built-in templates if CLI is unavailable. "
            "Supported templates: nextjs, vite-react, vite-vue, vite-svelte, flutter, "
            "expo (React Native), django, docker-fullstack, python, fastapi, express, node, static."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "description": (
                        "Project template: nextjs, vite-react, vite-vue, vite-svelte, "
                        "flutter, expo, django, docker-fullstack, python, fastapi, "
                        "express, node, static. Also accepts aliases like 'next', 'react', 'rn'."
                    ),
                },
                "project_path": {
                    "type": "string",
                    "description": "Directory where the project should be created.",
                },
                "project_name": {
                    "type": "string",
                    "description": "Project name (defaults to directory name).",
                },
                "project_description": {
                    "type": "string",
                    "description": "Short project description.",
                },
                "extra_files": {
                    "type": "object",
                    "description": "Additional files to create after scaffolding {path: content}.",
                },
            },
            "required": ["template", "project_path"],
        }

    async def execute(self, **kwargs: Any) -> str:
        template_key = kwargs.get("template", "").lower().strip()
        project_path = kwargs.get("project_path", "")
        project_name = kwargs.get("project_name", "") or Path(project_path).name
        description = kwargs.get("project_description", "") or f"A {template_key} project"
        extra_files: dict[str, str] = kwargs.get("extra_files", {}) or {}

        if not template_key:
            return "Error: template is required. Options: " + ", ".join(sorted(set(_TEMPLATE_ALIASES.values())))
        if not project_path:
            return "Error: project_path is required"

        # Resolve alias
        canonical = _TEMPLATE_ALIASES.get(template_key)
        if not canonical:
            return (
                f"Unknown template '{template_key}'. Available: "
                + ", ".join(sorted(set(_TEMPLATE_ALIASES.values())))
            )

        # Resolve path
        root = Path(project_path)
        if not root.is_absolute():
            workspace = Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
            root = workspace / root

        # Legacy types → delegate to CreateSoftwareTool
        if canonical in ("python", "node", "fastapi", "express", "static"):
            from sharanbot.agent.tools.create_software import CreateSoftwareTool
            tool = CreateSoftwareTool()
            return await tool.execute(
                project_path=str(root),
                files=extra_files or {"README.md": f"# {project_name}\n\n{description}\n"},
                project_type=canonical,
                project_name=project_name,
                project_description=description,
            )

        scaffolder = _CLI_SCAFFOLDERS.get(canonical)
        if not scaffolder:
            return f"No scaffolder configured for '{canonical}'"

        # Try CLI first
        cli_cmd = scaffolder.get("command")
        if cli_cmd:
            cli_result = await self._try_cli(
                cli_cmd, scaffolder, root, project_name, description,
            )
            if cli_result:
                # Write extra files on top
                if extra_files:
                    self._write_files(root, extra_files, project_name, description)
                # Auto-initialize Spec Kit for spec-driven development
                speckit_msg = await self._auto_speckit(root)
                if speckit_msg:
                    cli_result += f"\n\n📋 Spec Kit: {speckit_msg}"
                return cli_result

        # Fallback to built-in template
        fallback = scaffolder.get("fallback")
        if not fallback:
            return f"CLI scaffolder for '{canonical}' not available and no fallback template exists."

        root.mkdir(parents=True, exist_ok=True)
        count = self._write_files(root, fallback, project_name, description)

        # Write extra files
        if extra_files:
            count += self._write_files(root, extra_files, project_name, description)

        # Auto-initialize Spec Kit for spec-driven development
        speckit_msg = await self._auto_speckit(root)

        result = (
            f"✅ Scaffolded {canonical} project at {root} ({count} files, fallback template)\n"
            f"Next steps:\n"
            f"  cd {root}\n"
            f"  {self._install_cmd(canonical)}\n"
            f"  {self._dev_cmd(canonical)}"
        )
        if speckit_msg:
            result += f"\n\n📋 Spec Kit: {speckit_msg}"
        return result

    async def _try_cli(self, cmd_template: str, scaffolder: dict,
                       root: Path, name: str, description: str) -> str | None:
        """Try to run the real CLI scaffolder."""
        check_cmd = scaffolder.get("check", "")
        timeout = scaffolder.get("timeout", 60)

        # Check if CLI is available
        if check_cmd:
            try:
                proc = await asyncio.create_subprocess_shell(
                    check_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode != 0:
                    logger.debug("CLI check failed for: {}", check_cmd)
                    return None
            except Exception:
                return None

        # Run scaffolder
        cmd = cmd_template.format(path=str(root), name=name)
        logger.info("Running CLI scaffolder: {}", cmd)

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(input=b"\n"), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""

            if proc.returncode == 0:
                return (
                    f"✅ Scaffolded project at {root} (via CLI)\n"
                    f"Output:\n{output[-500:]}\n\n"
                    f"Next steps:\n"
                    f"  cd {root}\n"
                    f"  {self._dev_cmd(_resolve_canonical(scaffolder))}"
                )
            else:
                logger.warning("CLI scaffolder failed (code {}): {}", proc.returncode, output[-300:])
                return None
        except asyncio.TimeoutError:
            logger.warning("CLI scaffolder timed out after {}s", timeout)
            return None
        except Exception as e:
            logger.warning("CLI scaffolder error: {}", e)
            return None

    @staticmethod
    def _write_files(root: Path, files: dict[str, str], name: str, desc: str) -> int:
        """Write template files with variable substitution."""
        count = 0
        safe_name = name.replace("-", "_").replace(" ", "_").lower()
        for rel_path, content in files.items():
            # Substitute template variables
            actual_path = rel_path.replace("{name}", safe_name)
            try:
                rendered = content.format(name=name, description=desc)
            except (KeyError, IndexError):
                rendered = content.replace("{name}", name).replace("{description}", desc)

            full_path = root / actual_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(rendered, encoding="utf-8")
            count += 1
        return count

    @staticmethod
    async def _auto_speckit(root: Path) -> str | None:
        """Auto-initialize Spec Kit in a newly scaffolded project."""
        try:
            from sharanbot.agent.tools.specify_init import auto_init_speckit
            return await auto_init_speckit(root)
        except Exception as e:
            logger.debug("Spec Kit auto-init skipped: {}", e)
            return None

    @staticmethod
    def _install_cmd(template: str) -> str:
        node_based = {"nextjs", "vite-react", "vite-vue", "vite-svelte", "expo"}
        if template in node_based:
            return "npm install"
        if template == "flutter":
            return "flutter pub get"
        if template == "django":
            return "pip install -r requirements.txt"
        if template == "docker-fullstack":
            return "docker compose build"
        return "# install dependencies"

    @staticmethod
    def _dev_cmd(template: str) -> str:
        cmds = {
            "nextjs": "npm run dev",
            "vite-react": "npm run dev",
            "vite-vue": "npm run dev",
            "vite-svelte": "npm run dev",
            "flutter": "flutter run",
            "expo": "npx expo start",
            "django": "python manage.py runserver",
            "docker-fullstack": "docker compose up",
        }
        return cmds.get(template, "# start dev server")


def _resolve_canonical(scaffolder: dict) -> str:
    """Reverse-lookup canonical name from scaffolder dict."""
    for name, s in _CLI_SCAFFOLDERS.items():
        if s is scaffolder:
            return name
    return "unknown"
