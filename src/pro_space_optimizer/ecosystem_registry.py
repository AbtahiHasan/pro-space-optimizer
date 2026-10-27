"""Ecosystem / project-type detection registry.

This module recognises programming-language ecosystems and their frameworks
from **marker files** found in a project directory.  It is deliberately
declarative: a table of :class:`Ecosystem` definitions is evaluated by a small
engine, so new ecosystems can be added without touching the scanner.

Design principles
-----------------
* **Markers are evidence, not proof.**  A generic ``Makefile`` alone does not
  make a folder a C project.  Each definition declares a *confidence* and the
  engine prefers strong primary markers over weak generic ones.
* **No code execution.**  Detection never runs project code, build scripts,
  package managers or shell commands.  It only stats files and, when needed,
  reads *small* configuration files with bounded size limits.
* **Safe parsing.**  JSON/TOML/YAML/XML parsing is wrapped so malformed files
  never raise; a broken config simply yields no evidence.
* **No symlink following.**  Marker discovery uses ``os.lstat`` semantics and
  never descends through symlinks.
* **Deterministic.**  Given the same directory contents the result is always
  the same; ties are broken by a fixed confidence ordering and the registry
  order.

The result of detection is a :class:`Detection` describing the winning
ecosystem plus every ecosystem that matched, so the UI can show an
*ambiguous* state when more than one strong match is present.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

log = logging.getLogger(__name__)

# Maximum bytes we will read from any single marker file when content-based
# detection is required.  Keeps detection cheap and avoids huge files.
MAX_READ_BYTES = 64 * 1024


class Confidence(str, Enum):
    """How strongly a marker set implies a project of this ecosystem."""

    HIGH = "high"        # dedicated manifest, e.g. Cargo.toml, go.mod, pom.xml
    MEDIUM = "medium"    # strong but shared marker, e.g. package.json, Makefile
    LOW = "low"          # weak/generic marker needing supporting evidence


# Ordering used to break ties deterministically (higher wins).
_CONFIDENCE_RANK = {Confidence.HIGH: 3, Confidence.MEDIUM: 2, Confidence.LOW: 1}


# ---------------------------------------------------------------------------
# Small, defensive file readers
# ---------------------------------------------------------------------------

def _read_text(path: str, limit: int = MAX_READ_BYTES) -> Optional[str]:
    """Read at most *limit* bytes of a text file; None on any error."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit)
        return raw.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _read_json(path: str) -> Optional[dict]:
    text = _read_text(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _read_toml(path: str) -> Optional[dict]:
    text = _read_text(path)
    if text is None:
        return None
    try:
        try:
            import tomllib  # Python 3.11+
        except ModuleNotFoundError:  # pragma: no cover - older Pythons
            import tomli as tomllib  # type: ignore
        data = tomllib.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _read_yaml(path: str) -> Optional[dict]:
    """Parse a small YAML file if a parser is available; else None.

    We never add a hard dependency on PyYAML.  If it is not installed we fall
    back to a very small "does this key appear?" text scan in callers that only
    need key presence.
    """
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    text = _read_text(path)
    if text is None:
        return None
    try:
        data = yaml.safe_load(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Context passed to detection predicates
# ---------------------------------------------------------------------------

@dataclass
class ProjectContext:
    """Filesystem view of one candidate directory used for detection."""

    path: str
    _names: Optional[Set[str]] = None
    _cache: Dict[str, object] = field(default_factory=dict)

    def names(self) -> Set[str]:
        """Immediate entry names in the directory (no symlink traversal)."""
        if self._names is None:
            names: Set[str] = set()
            try:
                with os.scandir(self.path) as it:
                    for entry in it:
                        try:
                            if entry.is_symlink():
                                # Symlinked entries are visible by name but we
                                # never follow them.
                                names.add(entry.name)
                                continue
                            names.add(entry.name)
                        except OSError:
                            names.add(entry.name)
            except (OSError, ValueError):
                pass
            self._names = names
        return self._names

    def has(self, name: str) -> bool:
        return name in self.names()

    def has_suffix(self, suffix: str) -> bool:
        return any(n.endswith(suffix) for n in self.names())

    def has_prefix(self, prefix: str) -> bool:
        return any(n.startswith(prefix) for n in self.names())

    def has_glob(self, pattern: str) -> bool:
        import fnmatch
        return any(fnmatch.fnmatch(n, pattern) for n in self.names())

    def is_dir(self, name: str) -> bool:
        full = os.path.join(self.path, name)
        try:
            return os.path.isdir(full) and not os.path.islink(full)
        except OSError:
            return False

    def text(self, name: str, limit: int = MAX_READ_BYTES) -> Optional[str]:
        key = f"text:{name}"
        if key not in self._cache:
            self._cache[key] = _read_text(os.path.join(self.path, name), limit)
        return self._cache[key]  # type: ignore[return-value]

    def json(self, name: str) -> Optional[dict]:
        key = f"json:{name}"
        if key not in self._cache:
            self._cache[key] = _read_json(os.path.join(self.path, name))
        return self._cache[key]  # type: ignore[return-value]

    def toml(self, name: str) -> Optional[dict]:
        key = f"toml:{name}"
        if key not in self._cache:
            self._cache[key] = _read_toml(os.path.join(self.path, name))
        return self._cache[key]  # type: ignore[return-value]

    def yaml(self, name: str) -> Optional[dict]:
        key = f"yaml:{name}"
        if key not in self._cache:
            self._cache[key] = _read_yaml(os.path.join(self.path, name))
        return self._cache[key]  # type: ignore[return-value]

    def file_contains(self, name: str, needle: str,
                      limit: int = MAX_READ_BYTES) -> bool:
        text = self.text(name, limit)
        return bool(text) and needle in text

    def any_file_contains(self, names: Sequence[str], needle: str) -> bool:
        return any(self.file_contains(n, needle) for n in names)


# Predicate signature: (context) -> bool
Predicate = Callable[[ProjectContext], bool]


def _pkg_lists(name: str = "package.json") -> Callable[[ProjectContext], dict]:
    """Return a helper that extracts dependency dicts from a package.json."""
    def extract(ctx: ProjectContext) -> dict:
        data = ctx.json(name) or {}
        deps: Dict[str, str] = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            value = data.get(key)
            if isinstance(value, dict):
                deps.update(value)
        return deps
    return extract


def _pkg_has(dep: str) -> Predicate:
    def pred(ctx: ProjectContext) -> bool:
        return dep in _pkg_lists()(ctx)
    return pred


def _pkg_has_prefix(prefix: str) -> Predicate:
    def pred(ctx: ProjectContext) -> bool:
        deps = _pkg_lists()(ctx)
        return any(d == prefix or d.startswith(prefix) for d in deps)
    return pred


def _file_exists(name: str) -> Predicate:
    return lambda ctx: ctx.has(name)


def _any_file(names: Sequence[str]) -> Predicate:
    return lambda ctx: all(ctx.has(n) for n in names) if False else any(
        ctx.has(n) for n in names
    )


def _all_files(names: Sequence[str]) -> Predicate:
    return lambda ctx: all(ctx.has(n) for n in names)


def _file_has(name: str, needle: str) -> Predicate:
    return lambda ctx: ctx.file_contains(name, needle)


# Alias used by several registry entries.
_file_contains = _file_has


def _any_of(*preds: Predicate) -> Predicate:
    return lambda ctx: any(p(ctx) for p in preds)


def _all_of(*preds: Predicate) -> Predicate:
    return lambda ctx: all(p(ctx) for p in preds)


def _not(pred: Predicate) -> Predicate:
    return lambda ctx: not pred(ctx)


# ---------------------------------------------------------------------------
# Ecosystem definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ecosystem:
    """A detectable project ecosystem or framework."""

    id: str
    label: str
    markers: Tuple[str, ...]              # primary marker files
    supporting: Tuple[str, ...] = ()      # supporting / secondary markers
    confidence: Confidence = Confidence.MEDIUM
    detect: Optional[Predicate] = None    # optional extra evidence rule
    category: str = "general"
    # Language ecosystems that are "containers" (e.g. a JS repo) rather than
    # frameworks.  Used only for display ordering.
    parent: Optional[str] = None


@dataclass
class Detection:
    """Outcome of detecting the ecosystem of a directory."""

    ecosystem_id: Optional[str]
    label: str
    confidence: Optional[Confidence]
    ambiguous: bool = False
    matched: List[str] = field(default_factory=list)
    detail: str = ""

    @property
    def known(self) -> bool:
        return self.ecosystem_id is not None

    @property
    def display(self) -> str:
        if not self.known:
            return "Unknown"
        if self.ambiguous:
            return f"{self.label} (ambiguous)"
        return self.label


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

def _build_registry() -> List[Ecosystem]:
    E = Ecosystem
    C = Confidence
    reg: List[Ecosystem] = []

    # ===== JavaScript / TypeScript =====
    reg += [
        E("node", "Node.js", ("package.json",), confidence=C.HIGH,
          category="javascript"),
        E("npm", "npm", ("package-lock.json",), ("package.json",),
          category="javascript", parent="node"),
        E("yarn", "Yarn", ("yarn.lock",), ("package.json",),
          category="javascript", parent="node"),
        E("pnpm", "pnpm", ("pnpm-lock.yaml", "pnpm-workspace.yaml"),
          ("package.json",), category="javascript", parent="node"),
        E("bun", "Bun", ("bun.lock", "bun.lockb"), ("package.json",),
          category="javascript", parent="node"),
        E("deno", "Deno", ("deno.json", "deno.jsonc", "deno.lock"),
          category="javascript"),
        E("typescript", "TypeScript", ("tsconfig.json",),
          confidence=C.MEDIUM, category="javascript", parent="node"),
        E("angular", "Angular", ("angular.json",), ("package.json",),
          category="js-framework"),
        E("react", "React", ("package.json",),
          detect=_pkg_has("react"), confidence=C.MEDIUM,
          category="js-framework", parent="node"),
        E("nextjs", "Next.js",
          ("next.config.js", "next.config.mjs", "next.config.ts",
           "next.config.cjs"), ("package.json",), confidence=C.HIGH,
          category="js-framework", parent="node"),
        E("nuxt", "Nuxt", ("nuxt.config.ts", "nuxt.config.js",
                           "nuxt.config.mjs"), ("package.json",),
          confidence=C.HIGH, category="js-framework", parent="node"),
        E("vue", "Vue",
          ("vue.config.js",), confidence=C.HIGH, category="js-framework",
          detect=_any_of(_file_has("vite.config.ts", "vue"),
                         _pkg_has("vue"), _pkg_has_prefix("@vue/"))),
        E("svelte", "Svelte", ("svelte.config.js",), ("package.json",),
          confidence=C.HIGH, category="js-framework", parent="node"),
        E("sveltekit", "SvelteKit", ("svelte.config.js",),
          detect=_any_of(_pkg_has("@sveltejs/kit"),
                         _pkg_has_prefix("@sveltejs/kit")),
          confidence=C.HIGH, category="js-framework", parent="svelte"),
        E("astro", "Astro", ("astro.config.mjs", "astro.config.ts",
                             "astro.config.js"), ("package.json",),
          confidence=C.HIGH, category="js-framework", parent="node"),
        E("gatsby", "Gatsby", ("gatsby-config.js", "gatsby-config.ts"),
          ("package.json",), confidence=C.HIGH, category="js-framework",
          parent="node"),
        E("remix", "Remix / React Router",
          ("remix.config.js", "remix.config.ts", "react-router.config.ts"),
          detect=_any_of(_pkg_has("@remix-run/react"),
                         _pkg_has("@remix-run/node"),
                         _pkg_has("@react-router/dev")),
          confidence=C.HIGH, category="js-framework", parent="node"),
        E("nestjs", "NestJS", ("nest-cli.json",),
          detect=_any_of(_pkg_has("@nestjs/core"),
                         _pkg_has_prefix("@nestjs/")),
          confidence=C.HIGH, category="js-framework", parent="node"),
        E("express", "Express", ("package.json",), detect=_pkg_has("express"),
          confidence=C.MEDIUM, category="js-framework", parent="node"),
        E("electron", "Electron",
          ("electron-builder.yml", "electron-builder.json",
           "electron-builder.yaml"),
          detect=_pkg_has("electron"), confidence=C.HIGH,
          category="js-desktop", parent="node"),
        E("tauri", "Tauri", ("src-tauri/tauri.conf.json",
                             "src-tauri/tauri.conf.json5",
                             "src-tauri/Cargo.toml"),
          detect=_any_file(("src-tauri/tauri.conf.json",
                            "src-tauri/tauri.conf.json5")),
          confidence=C.HIGH, category="js-desktop"),
    ]

    # ===== PHP =====
    reg += [
        E("composer", "PHP / Composer", ("composer.json",),
          confidence=C.HIGH, category="php"),
        E("laravel", "Laravel", ("artisan",),
          detect=_any_of(_file_has("composer.json", "laravel/framework"),
                         _file_exists("artisan")),
          confidence=C.HIGH, category="php-framework", parent="composer"),
        E("symfony", "Symfony", ("symfony.lock", "bin/console"),
          detect=_any_of(_file_exists("symfony.lock"),
                         _file_has("composer.json", "symfony/")),
          confidence=C.HIGH, category="php-framework", parent="composer"),
        E("wordpress", "WordPress", ("wp-config.php", "wp-content",
                                     "wp-load.php"),
          confidence=C.HIGH, category="php-framework"),
        E("drupal", "Drupal", ("core/lib/Drupal.php", "core/composer.json"),
          detect=_any_of(_file_exists("core/lib/Drupal.php"),
                         _file_exists("core/composer.json")),
          confidence=C.HIGH, category="php-framework"),
        E("magento", "Magento", ("app/etc/env.php", "app/etc/config.php",
                                 "bin/magento"),
          confidence=C.HIGH, category="php-framework"),
        E("cakephp", "CakePHP", ("bin/cake",),
          detect=_any_of(_file_exists("bin/cake"),
                         _file_has("composer.json", "cakephp/")),
          confidence=C.HIGH, category="php-framework", parent="composer"),
        E("codeigniter", "CodeIgniter", ("spark", "system/core/CodeIgniter.php"),
          detect=_any_of(_file_exists("system/core/CodeIgniter.php"),
                         _file_has("composer.json", "codeigniter")),
          confidence=C.MEDIUM, category="php-framework", parent="composer"),
        E("yii", "Yii", ("yii",),
          detect=_any_of(_file_exists("yii"),
                         _file_has("composer.json", "yiisoft/")),
          confidence=C.HIGH, category="php-framework", parent="composer"),
        E("laminas", "Laminas", ("composer.json",),
          detect=_file_has("composer.json", "laminas/"),
          category="php-framework", parent="composer"),
        E("typo3", "TYPO3", ("typo3conf", "typo3", "typo3conf/LocalConfiguration.php"),
          confidence=C.HIGH, category="php-framework"),
    ]

    # ===== Python =====
    reg += [
        E("python", "Python", ("pyproject.toml", "setup.py", "setup.cfg",
                               "requirements.txt", "Pipfile",
                               "environment.yml"),
          confidence=C.HIGH, category="python"),
        E("pip", "pip", ("requirements.txt",), ("pyproject.toml",),
          category="python", parent="python"),
        E("pipenv", "Pipenv", ("Pipfile", "Pipfile.lock"),
          category="python", parent="python"),
        E("poetry", "Poetry", ("poetry.lock",),
          detect=_any_of(_file_exists("poetry.lock"),
                         _file_has("pyproject.toml", "[tool.poetry]")),
          category="python", parent="python"),
        E("uv", "uv", ("uv.lock",), category="python", parent="python"),
        E("pdm", "PDM", ("pdm.lock",), ("pyproject.toml",),
          category="python", parent="python"),
        E("tox", "tox", ("tox.ini",), category="python", parent="python"),
        E("nox", "Nox", ("noxfile.py",), category="python", parent="python"),
        E("conda", "Conda", ("environment.yml", "environment.yaml",
                             "conda-lock.yml"),
          category="python", parent="python"),
        E("django", "Django", ("manage.py",),
          detect=_any_of(_file_exists("manage.py"),
                         _py_meta_has("Django"),
                         _file_has("requirements.txt", "Django"),
                         _file_has("requirements.txt", "django")),
          confidence=C.HIGH, category="python-framework", parent="python"),
        E("flask", "Flask", (),
          detect=_any_of(_py_meta_has("Flask"),
                         _file_has("requirements.txt", "Flask"),
                         _file_has("requirements.txt", "flask")),
          confidence=C.MEDIUM, category="python-framework", parent="python"),
        E("fastapi", "FastAPI", (),
          detect=_any_of(_py_meta_has("fastapi"),
                         _file_has("requirements.txt", "fastapi")),
          confidence=C.MEDIUM, category="python-framework", parent="python"),
        E("pyramid", "Pyramid", (),
          detect=_any_of(_py_meta_has("pyramid"),
                         _file_has("requirements.txt", "pyramid")),
          category="python-framework", parent="python"),
        E("scrapy", "Scrapy", ("scrapy.cfg",),
          detect=_any_of(_file_exists("scrapy.cfg"),
                         _py_meta_has("Scrapy"),
                         _file_has("requirements.txt", "Scrapy")),
          confidence=C.HIGH, category="python-framework", parent="python"),
        E("celery", "Celery", (),
          detect=_any_of(_py_meta_has("celery"),
                         _file_has("requirements.txt", "celery")),
          category="python-framework", parent="python"),
        E("streamlit", "Streamlit", (),
          detect=_any_of(_py_meta_has("streamlit"),
                         _file_has("requirements.txt", "streamlit")),
          category="python-framework", parent="python"),
        E("gradio", "Gradio", (),
          detect=_any_of(_py_meta_has("gradio"),
                         _file_has("requirements.txt", "gradio")),
          category="python-framework", parent="python"),
        E("pytorch", "PyTorch", (),
          detect=_any_of(_py_meta_has("torch"),
                         _file_has("requirements.txt", "torch")),
          category="python-ml", parent="python"),
        E("tensorflow", "TensorFlow", (),
          detect=_any_of(_py_meta_has("tensorflow"),
                         _file_has("requirements.txt", "tensorflow")),
          category="python-ml", parent="python"),
        E("jupyter", "Jupyter", ("jupyter_notebook_config.py",
                                 ".jupyter"),
          detect=_any_of(_file_exists("jupyter_notebook_config.py"),
                         _file_exists(".jupyter"),
                         _any_glob("*.ipynb"),
                         _py_meta_has("jupyter")),
          confidence=C.MEDIUM, category="python-ml", parent="python"),
        E("scientific-python", "Scientific Python", (),
          detect=_any_of(_py_meta_has("numpy"), _py_meta_has("scipy"),
                         _py_meta_has("pandas"), _py_meta_has("scikit-learn"),
                         _file_has("requirements.txt", "numpy"),
                         _file_has("requirements.txt", "scipy"),
                         _file_has("requirements.txt", "pandas"),
                         _file_has("requirements.txt", "scikit-learn")),
          category="python-ml", parent="python"),
    ]

    # ===== Java / JVM =====
    reg += [
        E("maven", "Maven", ("pom.xml",), confidence=C.HIGH, category="jvm"),
        E("gradle", "Gradle", ("build.gradle", "build.gradle.kts",
                               "settings.gradle", "settings.gradle.kts",
                               "gradlew"),
          confidence=C.HIGH, category="jvm"),
        E("ant", "Ant", ("build.xml",), confidence=C.MEDIUM, category="jvm"),
        E("spring-boot", "Spring Boot", ("pom.xml", "build.gradle",
                                         "build.gradle.kts"),
          detect=_any_of(_file_has("pom.xml", "spring-boot"),
                         _file_has("build.gradle", "spring-boot"),
                         _file_has("build.gradle.kts", "spring-boot")),
          confidence=C.HIGH, category="jvm-framework", parent="maven"),
        E("kotlin", "Kotlin", ("build.gradle.kts", "settings.gradle.kts"),
          detect=_any_of(_file_has("build.gradle.kts", "kotlin"),
                         _file_exists("settings.gradle.kts")),
          confidence=C.MEDIUM, category="jvm", parent="gradle"),
        E("scala", "Scala / sbt", ("build.sbt",), confidence=C.HIGH,
          category="jvm"),
        E("leiningen", "Leiningen", ("project.clj",), confidence=C.HIGH,
          category="jvm"),
        E("clojure-cli", "Clojure CLI", ("deps.edn", "build.boot"),
          confidence=C.HIGH, category="jvm"),
        E("mill", "Mill", ("build.sc",), confidence=C.HIGH, category="jvm"),
        E("bazel-jvm", "Bazel (JVM)", ("MODULE.bazel", "WORKSPACE",
                                       "WORKSPACE.bazel"),
          detect=_any_of(_file_exists("MODULE.bazel"),
                         _file_exists("WORKSPACE"),
                         _file_exists("WORKSPACE.bazel")),
          confidence=C.MEDIUM, category="jvm"),
    ]

    # ===== C / C++ =====
    reg += [
        E("cmake", "CMake / C / C++", ("CMakeLists.txt", "CMakePresets.json"),
          confidence=C.HIGH, category="cpp"),
        E("make", "Make", ("Makefile", "makefile", "GNUmakefile"),
          confidence=C.LOW, category="cpp"),
        E("autotools", "Autotools", ("configure.ac", "configure",
                                     "Makefile.am"),
          confidence=C.MEDIUM, category="cpp"),
        E("meson", "Meson", ("meson.build", "meson_options.txt"),
          confidence=C.HIGH, category="cpp"),
        E("conan", "Conan", ("conanfile.py", "conanfile.txt"),
          confidence=C.HIGH, category="cpp"),
        E("vcpkg", "vcpkg", ("vcpkg.json",), confidence=C.HIGH,
          category="cpp"),
    ]

    # ===== Rust =====
    reg += [
        E("rust", "Rust", ("Cargo.toml", "Cargo.lock"), confidence=C.HIGH,
          category="rust"),
        E("cargo-config", "Cargo config", (".cargo/config.toml",
                                           ".cargo/config"),
          confidence=C.LOW, category="rust", parent="rust"),
        E("rust-toolchain", "Rust toolchain pin",
          ("rust-toolchain.toml", "rust-toolchain"), confidence=C.LOW,
          category="rust", parent="rust"),
        E("bevy", "Bevy", ("Cargo.toml",),
          detect=_file_has("Cargo.toml", "bevy"),
          confidence=C.MEDIUM, category="game", parent="rust"),
    ]

    # ===== Go =====
    reg += [
        E("go", "Go", ("go.mod",), confidence=C.HIGH, category="go"),
        E("go-workspace", "Go workspace", ("go.work",), confidence=C.MEDIUM,
          category="go", parent="go"),
        E("go-vendor", "Go (vendored)", ("vendor/modules.txt",),
          confidence=C.LOW, category="go", parent="go"),
    ]

    # ===== .NET / C# =====
    reg += [
        E("dotnet", ".NET / C#",
          ("*.csproj", "*.fsproj", "*.vbproj", "*.sln", "*.slnx"),
          ("global.json", "Directory.Build.props",
           "Directory.Build.targets"),
          confidence=C.HIGH, category="dotnet"),
        E("aspnet", "ASP.NET Core", ("*.csproj",),
          detect=_file_contains_glob("*.csproj", "Microsoft.NET.Sdk.Web"),
          confidence=C.MEDIUM, category="dotnet-framework", parent="dotnet"),
        E("nuget", "NuGet", ("packages.config",), ("*.csproj",),
          confidence=C.MEDIUM, category="dotnet", parent="dotnet"),
    ]

    # ===== Ruby =====
    reg += [
        E("ruby", "Ruby", ("Gemfile", "Gemfile.lock", "Rakefile",
                           ".ruby-version"),
          confidence=C.HIGH, category="ruby"),
        E("rails", "Ruby on Rails", ("bin/rails",),
          detect=_any_of(_file_exists("bin/rails"),
                         _file_has("Gemfile", "rails")),
          confidence=C.HIGH, category="ruby-framework", parent="ruby"),
        E("jekyll", "Jekyll", ("_config.yml",),
          detect=_any_of(_file_has("Gemfile", "jekyll"),
                         _file_exists("_config.yml")),
          confidence=C.MEDIUM, category="ruby-framework", parent="ruby"),
        E("sinatra", "Sinatra", (),
          detect=_file_has("Gemfile", "sinatra"),
          category="ruby-framework", parent="ruby"),
        E("bundler", "Bundler", (".bundle/config",), ("Gemfile",),
          confidence=C.LOW, category="ruby", parent="ruby"),
    ]

    # ===== Elixir / Erlang / Gleam =====
    reg += [
        E("elixir", "Elixir / Mix", ("mix.exs", "mix.lock"),
          confidence=C.HIGH, category="elixir"),
        E("phoenix", "Phoenix", ("mix.exs",),
          detect=_file_has("mix.exs", ":phoenix"),
          confidence=C.HIGH, category="elixir-framework", parent="elixir"),
        E("erlang", "Erlang", ("rebar.config", "rebar.lock", "erlang.mk",
                               "rebar3"),
          confidence=C.HIGH, category="erlang"),
        E("gleam", "Gleam", ("gleam.toml",), confidence=C.HIGH,
          category="erlang"),
    ]

    # ===== Dart / Flutter =====
    reg += [
        E("dart", "Dart", ("pubspec.yaml", "pubspec.lock",
                           "analysis_options.yaml"),
          confidence=C.HIGH, category="dart"),
        E("flutter", "Flutter", ("pubspec.yaml",),
          detect=_any_of(_file_exists("lib/main.dart"),
                         _file_contains("pubspec.yaml", "flutter"),
                         _all_of(_file_exists("pubspec.yaml"),
                                 _any_of(_dir_exists("android"),
                                         _dir_exists("ios")))),
          confidence=C.HIGH, category="dart-framework", parent="dart"),
    ]

    # ===== Swift / Apple =====
    reg += [
        E("swiftpm", "Swift Package Manager", ("Package.swift",
                                               "Package.resolved"),
          confidence=C.HIGH, category="apple"),
        E("xcode", "Xcode project", ("*.xcodeproj/project.pbxproj",
                                     "*.xcworkspace/contents.xcworkspacedata"),
          confidence=C.HIGH, category="apple"),
        E("cocoapods", "CocoaPods", ("Podfile", "Podfile.lock"),
          confidence=C.HIGH, category="apple"),
        E("carthage", "Carthage", ("Cartfile", "Cartfile.resolved"),
          confidence=C.HIGH, category="apple"),
        E("objc", "Objective-C", ("*.xcodeproj/project.pbxproj",),
          confidence=C.LOW, category="apple"),
    ]

    # ===== Android / Kotlin Multiplatform =====
    reg += [
        E("android", "Android",
          ("AndroidManifest.xml", "gradle/wrapper/gradle-wrapper.properties",
           "libs.versions.toml", "settings.gradle", "settings.gradle.kts"),
          detect=_any_of(_file_exists("AndroidManifest.xml"),
                         _file_exists("gradle/wrapper/gradle-wrapper.properties"),
                         _file_exists("libs.versions.toml")),
          confidence=C.HIGH, category="mobile"),
        E("kmp", "Kotlin Multiplatform", ("build.gradle.kts",),
          detect=_file_contains("build.gradle.kts", "multiplatform"),
          confidence=C.MEDIUM, category="mobile"),
    ]

    # ===== R =====
    reg += [
        E("r", "R", ("DESCRIPTION", "renv.lock", ".Rproj",
                     "packrat.lock", "*.Rproj"),
          confidence=C.HIGH, category="r"),
    ]

    # ===== Julia =====
    reg += [
        E("julia", "Julia", ("Project.toml", "Manifest.toml"),
          confidence=C.HIGH, category="julia"),
    ]

    # ===== Haskell =====
    reg += [
        E("haskell", "Haskell", ("*.cabal", "cabal.project", "stack.yaml",
                                 "package.yaml"),
          confidence=C.HIGH, category="haskell"),
    ]

    # ===== OCaml =====
    reg += [
        E("ocaml", "OCaml", ("dune-project", "dune", "*.opam"),
          confidence=C.HIGH, category="ocaml"),
    ]

    # ===== Clojure (non-JVM display too) =====
    # (already added under jvm: leiningen/clojure-cli)

    # ===== Perl =====
    reg += [
        E("perl", "Perl", ("Makefile.PL", "Build.PL", "cpanfile", "META.json",
                           "META.yml"),
          confidence=C.HIGH, category="perl"),
    ]

    # ===== Lua =====
    reg += [
        E("lua", "Lua / LuaRocks", ("*.rockspec", "luarocks.lock"),
          ("stylua.toml",), confidence=C.HIGH, category="lua"),
    ]

    # ===== Fortran =====
    reg += [
        E("fortran", "Fortran", ("fpm.toml",),
          detect=_any_of(_file_exists("fpm.toml"),
                         _file_contains("CMakeLists.txt", "Fortran"),
                         _file_contains("meson.build", "fortran")),
          confidence=C.MEDIUM, category="fortran"),
    ]

    # ===== Zig / Nim / Crystal / D / V =====
    reg += [
        E("zig", "Zig", ("build.zig", "build.zig.zon"), confidence=C.HIGH,
          category="systems"),
        E("nim", "Nim", ("*.nimble",), confidence=C.HIGH, category="systems"),
        E("crystal", "Crystal", ("shard.yml", "shard.lock"),
          confidence=C.HIGH, category="systems"),
        E("dlang", "D", ("dub.json", "dub.sdl"), confidence=C.HIGH,
          category="systems"),
        E("vlang", "V", ("v.mod",), confidence=C.HIGH, category="systems"),
    ]

    # ===== Solidity / Ethereum =====
    reg += [
        E("hardhat", "Hardhat", ("hardhat.config.js", "hardhat.config.ts"),
          confidence=C.HIGH, category="blockchain"),
        E("foundry", "Foundry", ("foundry.toml",), confidence=C.HIGH,
          category="blockchain"),
        E("truffle", "Truffle", ("truffle-config.js",), confidence=C.HIGH,
          category="blockchain"),
        E("brownie", "Brownie", ("brownie-config.yaml", "brownie-config.yml"),
          confidence=C.HIGH, category="blockchain"),
        E("ape", "Ape", ("ape-config.yaml", "ape-config.yml"),
          confidence=C.HIGH, category="blockchain"),
    ]

    # ===== Infrastructure as Code / DevOps =====
    reg += [
        E("terraform", "Terraform", ("*.tf", ".terraform.lock.hcl"),
          confidence=C.HIGH, category="iac"),
        E("opentofu", "OpenTofu", (".terraform.lock.hcl",),
          detect=_any_glob("*.tf"), confidence=C.MEDIUM, category="iac"),
        E("ansible", "Ansible", ("ansible.cfg", "playbook.yml",
                                 "site.yml", "inventory"),
          confidence=C.MEDIUM, category="iac"),
        E("pulumi", "Pulumi", ("Pulumi.yaml", "Pulumi.yml"),
          confidence=C.HIGH, category="iac"),
        E("kubernetes", "Kubernetes", ("kustomization.yaml", "kustomization.yml"),
          confidence=C.MEDIUM, category="iac"),
        E("helm", "Helm", ("Chart.yaml",), confidence=C.HIGH, category="iac"),
        E("docker-compose", "Docker Compose",
          ("compose.yaml", "compose.yml", "docker-compose.yaml",
           "docker-compose.yml"), confidence=C.HIGH, category="iac"),
        E("devcontainer", "Dev Containers",
          (".devcontainer/devcontainer.json",), confidence=C.MEDIUM,
          category="iac"),
        E("nix", "Nix", ("flake.nix", "default.nix"), confidence=C.HIGH,
          category="iac"),
        E("vagrant", "Vagrant", ("Vagrantfile",), confidence=C.HIGH,
          category="iac"),
        E("packer", "Packer", ("*.pkr.hcl", "*.pkr.json"),
          confidence=C.HIGH, category="iac"),
    ]

    # ===== Build systems / monorepos =====
    reg += [
        E("bazel", "Bazel", ("MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel",
                             "BUILD", "BUILD.bazel"),
          confidence=C.HIGH, category="build"),
        E("buck", "Buck", (".buckconfig", "BUCK"), confidence=C.HIGH,
          category="build"),
        E("pants", "Pants", ("pants.toml",), confidence=C.HIGH,
          category="build"),
        E("nx", "Nx", ("nx.json",), confidence=C.HIGH, category="monorepo"),
        E("turborepo", "Turborepo", ("turbo.json",), confidence=C.HIGH,
          category="monorepo"),
        E("lerna", "Lerna", ("lerna.json",), confidence=C.HIGH,
          category="monorepo"),
        E("rush", "Rush", ("rush.json",), confidence=C.HIGH,
          category="monorepo"),
        E("lage", "Lage", ("lage.config.js", "lage.config.ts"),
          confidence=C.HIGH, category="monorepo"),
        E("task", "Task", ("Taskfile.yml", "Taskfile.yaml"),
          confidence=C.HIGH, category="build"),
        E("just", "Just", ("justfile", "Justfile", ".justfile"),
          confidence=C.HIGH, category="build"),
        E("earthly", "Earthly", ("Earthfile",), confidence=C.HIGH,
          category="build"),
    ]

    # ===== Docs / static sites =====
    reg += [
        E("hugo", "Hugo", ("hugo.toml", "hugo.yaml", "hugo.yml",
                           "config.toml"),
          detect=_any_of(_file_exists("hugo.toml"), _file_exists("hugo.yaml"),
                         _file_exists("hugo.yml"),
                         _all_of(_file_exists("config.toml"),
                                 _dir_exists("content"))),
          confidence=C.MEDIUM, category="docs"),
        E("mkdocs", "MkDocs", ("mkdocs.yml", "mkdocs.yaml"),
          confidence=C.HIGH, category="docs"),
        E("docusaurus", "Docusaurus",
          ("docusaurus.config.js", "docusaurus.config.ts",
           "docusaurus.config.mjs"),
          confidence=C.HIGH, category="docs"),
        E("sphinx", "Sphinx", ("conf.py",),
          detect=_any_of(_file_exists("conf.py"),
                         _file_exists("docs/conf.py")),
          confidence=C.MEDIUM, category="docs"),
        E("zola", "Zola", ("config.toml",),
          detect=_all_of(_file_exists("config.toml"),
                         _dir_exists("themes")),
          confidence=C.LOW, category="docs"),
        E("vitepress", "VitePress", (".vitepress/config.js",
                                     ".vitepress/config.ts"),
          confidence=C.HIGH, category="docs"),
        E("vuepress", "VuePress", (".vuepress/config.js",
                                   ".vuepress/config.ts"),
          confidence=C.HIGH, category="docs"),
        E("eleventy", "Eleventy", (".eleventy.js", "eleventy.config.js",
                                   "eleventy.config.cjs"),
          confidence=C.HIGH, category="docs"),
    ]

    # ===== Data science / ML / AI =====
    reg += [
        E("mlflow", "MLflow", ("MLproject", "MLproject.yaml"),
          confidence=C.HIGH, category="mlops"),
        E("dvc", "DVC", ("dvc.yaml", "dvc.lock", ".dvc/config"),
          confidence=C.HIGH, category="mlops"),
        E("kedro", "Kedro", ("conf/base/catalog.yml", "conf/base/catalog.yaml"),
          detect=_any_of(_file_exists("conf/base/catalog.yml"),
                         _file_exists("conf/base/catalog.yaml")),
          confidence=C.HIGH, category="mlops"),
        E("huggingface", "Hugging Face", ("config.json",),
          detect=_any_of(_file_exists("config.json"),
                         _file_contains("requirements.txt", "transformers"),
                         _file_contains("requirements.txt", "huggingface")),
          confidence=C.LOW, category="mlops"),
    ]

    # ===== Game development =====
    reg += [
        E("unity", "Unity", ("Assets", "ProjectSettings/ProjectVersion.txt"),
          detect=_all_of(_dir_exists("Assets"),
                         _file_exists("ProjectSettings/ProjectVersion.txt")),
          confidence=C.HIGH, category="game"),
        E("unreal", "Unreal Engine", ("*.uproject",), confidence=C.HIGH,
          category="game"),
        E("godot", "Godot", ("project.godot",), confidence=C.HIGH,
          category="game"),
        E("gamemaker", "GameMaker", ("*.yyp",), confidence=C.HIGH,
          category="game"),
        E("love2d", "LÖVE", ("conf.lua", "main.lua"),
          confidence=C.MEDIUM, category="game"),
    ]

    # ===== General / emerging web tooling =====
    reg += [
        E("webpack", "Webpack", ("webpack.config.js", "webpack.config.ts",
                                 "webpack.config.cjs",
                                 "webpack.config.mjs"),
          confidence=C.HIGH, category="js-tooling"),
        E("rollup", "Rollup", ("rollup.config.js", "rollup.config.mjs",
                               "rollup.config.ts"),
          confidence=C.HIGH, category="js-tooling"),
        E("parcel", "Parcel", (".parcelrc",),
          detect=_any_of(_file_exists(".parcelrc"),
                         _pkg_has("parcel")),
          confidence=C.MEDIUM, category="js-tooling", parent="node"),
        E("esbuild", "esbuild", (),
          detect=_any_of(_pkg_has("esbuild"),
                         _file_exists("esbuild.config.js")),
          category="js-tooling", parent="node"),
        E("swc", "SWC", (".swcrc",), confidence=C.MEDIUM, category="js-tooling"),
        E("biome", "Biome", ("biome.json", "biome.jsonc"),
          confidence=C.MEDIUM, category="js-tooling"),
        E("eslint", "ESLint", ("eslint.config.js", "eslint.config.mjs",
                               "eslint.config.cjs", ".eslintrc",
                               ".eslintrc.js", ".eslintrc.json",
                               ".eslintrc.yml", ".eslintrc.yaml",
                               ".eslintrc.cjs"),
          confidence=C.LOW, category="js-tooling"),
        E("prettier", "Prettier", (".prettierrc", ".prettierrc.json",
                                   ".prettierrc.yml", ".prettierrc.yaml",
                                   ".prettierrc.js", "prettier.config.js",
                                   "prettier.config.cjs", ".prettierrc.cjs"),
          confidence=C.LOW, category="js-tooling"),
        E("lefthook", "Lefthook", ("lefthook.yml", "lefthook.yaml"),
          confidence=C.LOW, category="js-tooling"),
        E("husky", "Husky", (".husky",), ("package.json",),
          confidence=C.LOW, category="js-tooling", parent="node"),
        E("wasm", "WebAssembly", ("Cargo.toml", "wasm-pack.toml",
                                  "wasm-bindgen.toml"),
          detect=_any_of(_file_exists("wasm-pack.toml"),
                         _file_contains("Cargo.toml", "wasm-bindgen"),
                         _file_contains("Cargo.toml", "wasm-bindgen-futures")),
          confidence=C.MEDIUM, category="systems"),
    ]

    return reg


# Helpers referencing _any_glob/_dir_exists defined after use above are looked
# up at call time; define them before _build_registry is invoked.
def _any_glob(pattern: str) -> Predicate:
    return lambda ctx: ctx.has_glob(pattern)


def _dir_exists(name: str) -> Predicate:
    return lambda ctx: ctx.is_dir(name)


def _file_contains_glob(pattern: str, needle: str) -> Predicate:
    def pred(ctx: ProjectContext) -> bool:
        import fnmatch
        for nm in ctx.names():
            if fnmatch.fnmatch(nm, pattern):
                if ctx.file_contains(nm, needle):
                    return True
        return False
    return pred


def _py_meta_has(package: str) -> Predicate:
    """True if any Python dependency manifest lists *package* (case-fold)."""
    needle = package.lower()

    def pred(ctx: ProjectContext) -> bool:
        data = ctx.toml("pyproject.toml")
        if data:
            if _toml_mentions(data, needle):
                return True
        for req in ("requirements.txt", "requirements-dev.txt",
                    "environment.yml", "environment.yaml", "setup.py",
                    "setup.cfg", "Pipfile"):
            text = ctx.text(req)
            if text and needle in text.lower():
                return True
        return False

    return pred


def _toml_mentions(data: dict, needle: str) -> bool:
    text_parts: List[str] = []

    def walk(obj) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                text_parts.append(str(k).lower())
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)
        elif isinstance(obj, str):
            text_parts.append(obj.lower())

    walk(data)
    return any(needle in part for part in text_parts)


# ---------------------------------------------------------------------------
# Registry access + detection engine
# ---------------------------------------------------------------------------

_REGISTRY: Optional[List[Ecosystem]] = None


def registry() -> List[Ecosystem]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def ecosystem_ids() -> List[str]:
    return [e.id for e in registry()]


def get_ecosystem(eco_id: str) -> Optional[Ecosystem]:
    for e in registry():
        if e.id == eco_id:
            return e
    return None


def _marker_present(ctx: ProjectContext, marker: str) -> bool:
    """Return True if *marker* is present (supports ``*`` glob patterns)."""
    if marker == "*.tf":
        return ctx.has_glob("*.tf")
    if marker.endswith(".tf"):
        return ctx.has_glob(marker)
    if "*" in marker:
        # Support patterns like "*.csproj" or "*.xcodeproj/project.pbxproj".
        if marker.startswith("*.") and marker.count("*") == 1:
            suffix = marker[1:]  # e.g. ".csproj"
            if "/" in marker:
                # e.g. "*.xcodeproj/project.pbxproj"
                dir_suffix, _, file_name = marker.partition("/")
                for n in ctx.names():
                    if n.endswith(dir_suffix[1:]) and ctx.is_dir(n):
                        if os.path.exists(os.path.join(ctx.path, n, file_name)):
                            return True
                return False
            return ctx.has_suffix(suffix)
        return ctx.has_glob(marker)
    return ctx.has(marker)


def _evaluate(eco: Ecosystem, ctx: ProjectContext) -> bool:
    """Return True if *eco* matches the directory described by *ctx*."""
    primary_present = any(_marker_present(ctx, m) for m in eco.markers)

    # Extra evidence rule (framework specificity, dependencies, ...).
    if eco.detect is not None:
        try:
            if not eco.detect(ctx):
                return False
        except Exception:  # pragma: no cover - defensive
            return False
        # Some detect rules are self-sufficient (no primary markers); if the
        # rule passed we accept even without a declared marker.
        return True

    if not primary_present:
        return False

    # Supporting markers strengthen confidence but do not veto a primary hit.
    return True


def detect(path: str) -> Detection:
    """Detect the ecosystem of the directory at *path*.

    Returns a :class:`Detection`.  When several strong ecosystems match the
    result is marked *ambiguous* but still names the highest-confidence,
    earliest-registered match so the UI always has something to show.
    """
    ctx = ProjectContext(path=path)
    matched: List[Ecosystem] = []
    try:
        for eco in registry():
            if _evaluate(eco, ctx):
                matched.append(eco)
    except Exception:  # pragma: no cover - defensive
        log.exception("ecosystem detection failed for %s", path)
        return Detection(None, "Unknown", None)

    if not matched:
        return Detection(None, "Unknown", None)

    # Compute the "specificity" of each match: a framework whose parent is also
    # present is *more specific* and should win.  We count how deep a chain of
    # parent relationships a match sits on (language=0, framework=1, and so on)
    # and add it to the confidence rank so a framework outranks its container.
    def specificity(eco: Ecosystem) -> int:
        depth = 0
        parent = eco.parent
        seen: Set[str] = set()
        while parent and parent not in seen:
            seen.add(parent)
            depth += 1
            parent_eco = get_ecosystem(parent)
            parent = parent_eco.parent if parent_eco else None
        return depth

    def score(eco: Ecosystem) -> int:
        return _CONFIDENCE_RANK[eco.confidence] * 100 + specificity(eco)

    # Rank by (confidence, specificity), then registry order for determinism.
    best = max(matched, key=score)

    # Ambiguity: more than one match shares the *same* top score, and they are
    # not in an ancestor/descendant relationship with one another.
    top_score = max(score(e) for e in matched)
    top_tier = [e for e in matched if score(e) == top_score]

    def related(a: Ecosystem, b: Ecosystem) -> bool:
        # True if either is an ancestor of the other via the parent chain.
        def chain(e: Ecosystem) -> List[str]:
            out: List[str] = []
            seen: Set[str] = set()
            p = e.parent
            while p and p not in seen:
                out.append(p)
                seen.add(p)
                pe = get_ecosystem(p)
                p = pe.parent if pe else None
            return out
        return a.id in chain(b) or b.id in chain(a)

    unrelated_top = [e for e in top_tier if e is not best]
    ambiguous = any(not related(best, e) for e in unrelated_top)

    detail = ", ".join(e.label for e in matched)
    return Detection(
        ecosystem_id=best.id,
        label=best.label,
        confidence=best.confidence,
        ambiguous=ambiguous,
        matched=[e.id for e in matched],
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Ecosystem-aware candidate folder rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FolderRule:
    """A known generated/dependency folder name for a set of ecosystems.

    These names only identify *candidates*.  Deletion still requires explicit
    user action and confirmation; we never assume a name alone proves a folder
    is disposable.
    """

    name: str
    ecosystems: Tuple[str, ...]     # ecosystem ids this folder is typical for
    description: str = ""


def _build_folder_rules() -> List[FolderRule]:
    R = FolderRule
    rules: List[FolderRule] = []
    rules += [
        R("node_modules", ("node", "npm", "yarn", "pnpm", "bun", "deno",
                           "nextjs", "nuxt", "vue", "svelte", "sveltekit",
                           "astro", "gatsby", "react", "express", "nestjs",
                           "angular", "remix", "electron", "parcel", "esbuild",
                           "webpack", "rollup"), "JS deps"),
        R("vendor", ("composer", "laravel", "symfony", "cakephp",
                     "codeigniter", "yii", "laminas", "go", "ruby", "rails"),
          "third-party deps"),
        R(".venv", ("python", "poetry", "uv", "pdm", "pipenv"), "Python virtualenv"),
        R("venv", ("python", "poetry", "uv", "pdm", "pipenv"), "Python virtualenv"),
        R("env", ("python",), "Python virtualenv"),
        R("__pycache__", ("python",), "bytecode cache"),
        R(".pytest_cache", ("python",), "pytest cache"),
        R(".mypy_cache", ("python",), "mypy cache"),
        R(".ruff_cache", ("python",), "ruff cache"),
        R(".tox", ("python", "tox"), "tox env"),
        R(".nox", ("python", "nox"), "nox env"),
        R("__pypackages__", ("python", "pdm"), "PEP 582 packages"),
        R("target", ("rust", "cargo-config", "bevy", "maven", "scala"), "build output"),
        R("build", (), "build output"),
        R("dist", (), "distribution output"),
        R("out", (), "build output"),
        R("bin", (), "build output"),
        R("obj", ("dotnet", "aspnet"), ".NET intermediate"),
        R(".next", ("nextjs",), "Next.js build"),
        R(".nuxt", ("nuxt",), "Nuxt build"),
        R(".svelte-kit", ("sveltekit", "svelte"), "SvelteKit output"),
        R(".angular", ("angular",), "Angular cache"),
        R("coverage", (), "coverage output"),
        R(".nyc_output", ("node",), "nyc output"),
        R("Pods", ("cocoapods",), "CocoaPods deps"),
        R("Carthage", ("carthage",), "Carthage deps"),
        R(".dart_tool", ("dart", "flutter"), "Dart tooling"),
        R("flutter_derived", ("flutter",), "Flutter derived"),
        R("DerivedData", ("xcode", "swiftpm", "objc"), "Xcode derived data"),
        R(".gradle", ("gradle", "android", "kotlin", "kmp"), "Gradle cache"),
        R(".terraform", ("terraform", "opentofu"), "Terraform providers"),
        R(".pulumi", ("pulumi",), "Pulumi state"),
        R(".serverless", (), "Serverless cache"),
        R(".turbo", ("turborepo",), "Turborepo cache"),
        R(".nx", ("nx",), "Nx cache"),
        R(".cache", (), "generic cache"),
        R(".parcel-cache", ("parcel",), "Parcel cache"),
        R(".vite", ("vue", "svelte", "astro"), "Vite cache"),
        R(".eggs", ("python",), "setuptools artifacts"),
        R("htmlcov", ("python",), "coverage html"),
    ]
    return rules


_FOLDER_RULES: Optional[List[FolderRule]] = None


def folder_rules() -> List[FolderRule]:
    global _FOLDER_RULES
    if _FOLDER_RULES is None:
        _FOLDER_RULES = _build_folder_rules()
    return _FOLDER_RULES


def ecosystem_folder_names() -> List[str]:
    """All candidate folder names known to the registry (deduplicated)."""
    seen: List[str] = []
    for rule in folder_rules():
        if rule.name not in seen:
            seen.append(rule.name)
    return seen
