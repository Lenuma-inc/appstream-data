# AppStream catalogs for LunaOS

Этот репозиторий собирает только дополнительные каталоги. Пакет
`archlinux-appstream-data` используется без изменений.

| Пакет | Содержимое |
| --- | --- |
| `lunaos-appstream-data` | Каталог и иконки `lunaos-repo` |
| `chaotic-aur-appstream-data` | Каталог и иконки `chaotic-aur` |
| `lunaos-appstream-data-common` | Метапакет с зависимостями на оба каталога и `archlinux-appstream-data` |

`libpamac-lunaos` должен зависеть от `lunaos-appstream-data-common`.
Приоритет дополнительных метаданных: LunaOS `20`, Chaotic-AUR `-10`;
не заданный приоритет каталога Arch принимается за `0`. Это приоритет
метаданных, выбор устанавливаемого пакета по-прежнему выполняет libalpm.

## Почему appstream-generator

Используется `appstream-generator >= 0.10.2` с backend `archlinux`, как у Arch.
Обвязка `generate.py` отвечает за загрузку, фильтрацию, проверку и упаковку.
Обработка metainfo, desktop-файлов, изображений и формата AppStream остаётся
за upstream-генератором. Генератор ALT интересен списками отбора и подключением
дополнительных ресурсов, но переносить его обработку RPM для этой задачи не нужно.

Документация:

- [Инструкция Arch](https://mirror.math.princeton.edu/pub/archlinux/other/packages/archlinux-appstream-data/HOWTO)
- [Конфигурация appstream-generator](https://github.com/ximion/appstream-generator/blob/master/docs/asgen-config.md)
- [Настройки отбора в ALT](https://bugzilla.altlinux.org/show_bug.cgi?id=37021)

## Настройки видимости

Все правила находятся в `policy.json`. По умолчанию:

- `exclude_packages: ["*-git"]` — исключить пакеты по маске;
- `keep_packages: []` — точные имена исключений из правил скрытия;
- `hide_binary_if_base_exists: true` — скрыть `foo-bin`, если существует `foo`;
- `binary_suffixes: ["-bin"]` — суффиксы для этого сравнения;
- `exclude_components: []` — маски AppStream ID, скрываемых при упаковке.

При финальной валидации компоненты с ошибками автоматически исключаются, затем каталог
проверяется повторно. Предупреждения не мешают публикации. Ошибки всего каталога и
пустой результат останавливают сборку. Полный отчёт всех проверок сохраняется в
`dist/<имя-пакета>/validation.yaml`, включая причины автоматических исключений.

Например, следующий фрагмент оставляет нужный git-пакет:

```json
{
  "keep_packages": ["quickshell-git"]
}
```

Это фрагмент для редактирования соответствующих полей, а не замена всего файла.
Исключение через `keep_packages` имеет наивысший приоритет. Без обычного `foo`
пакет `foo-bin` остаётся видимым. Для полного исключения `-bin` добавьте `"*-bin"`
в `exclude_packages`. `keep_packages` не добавляет в каталог пакеты без пригодных
метаданных или ресурсов.

При сравнении имён учитываются оба дополнительных репозитория и базы
`core`, `extra`, `multilib`. Из Arch скачиваются только индексы пакетов,
его каталог не генерируется и не фильтруется. Поэтому наши правила не могут
скрыть карточку, которая уже содержится в `archlinux-appstream-data`.

Все компоненты с повторяющимся ID исключаются при упаковке; их ID выводятся в лог.

## Локальная генерация

Установите зависимости на сборочной Arch-системе:

```sh
sudo pacman -Syu --needed appstream-generator appstream python python-yaml libarchive \
  libheif libjxl openslide poppler-glib imagemagick svt-av1 rav1e ffmpeg base-devel
```

Посмотрите отбор и объём загрузки по уже имеющимся базам `.files`:

```sh
python generate.py plan --explain
```

`plan` требует базы обоих дополнительных репозиториев `.files` и базы Arch
`.db` в `/var/lib/pacman/sync`. Он не обновляет системные базы. Для другого
расположения используйте `--database-dir`.

Соберите сначала каталог LunaOS:

```sh
python generate.py all --repo lunaos-repo --explain
cd dist/lunaos-appstream-data
makepkg
```

Для обоих каталогов:

```sh
python generate.py all --explain
(cd dist/lunaos-appstream-data && makepkg)
(cd dist/chaotic-aur-appstream-data && makepkg)
(cd packages/lunaos-appstream-data-common && makepkg)
```

Можно запускать стадии отдельно: `sync`, `generate`, `package`.
`--version 20261022 --release 2` задают версию выходных пакетов.
После изменения политики снова выполните `sync` и `generate`.
Стадия упаковки отказывается использовать результаты для другого набора входных
данных. Фактические SHA-256 исходных архивов автоматически записываются в PKGBUILD.

Корневой `PKGBUILD` — split package для сборки всех трёх пакетов существующим
buildbot `lunaos-repo`:

```sh
makepkg -s
```

Он рассчитан на полный git checkout: запускает генератор из `$startdir`, затем
упаковывает результаты. Генерация требует сети во время `build()`. Сборка
из отдельного source-пакета `makepkg -S` этим PKGBUILD не поддерживается;
для самостоятельного переноса используйте готовые каталоги и PKGBUILD из `dist`.
`APPSTREAM_WORK_DIR=/путь/к/кешу` сохраняет кеш между сборками корневого PKGBUILD.

## Загрузка и кеш

Источники определены в `repositories.json`. Из `.files` выбираются пакеты с
metainfo, desktop-файлами, иконками и шрифтами. Поля `desc` и `files` берутся из
одного снимка, поэтому они не расходятся при обновлении зеркала.

Каждый полный пакет скачивается по HTTPS и сверяется с SHA-256 из индекса.
Индекс получается по HTTPS; проверка GPG-подписи индекса пока не реализована.
Полный архив затем преобразуется в небольшой внутренний архив ресурсов и удаляется.
Внутренний индекс получает новый размер и SHA-256; исходный хеш сохраняется в
`manifest.json`. Эти архивы ресурсов не устанавливаются через pacman.

Для дополнительных ресурсов из пакета без стандартных путей добавьте его точное
имя в `include_packages` нужного репозитория в `repositories.json`.
По умолчанию загружается до четырёх пакетов параллельно (`--jobs`).
Объём, показанный `plan`, — полный сетевой объём до учёта кеша, а не размер
итогового пакета. `work/` содержит кеш и отчёты, `dist/` — выходные материалы.
Кеш не очищается автоматически; удалить его можно вне запущенной генерации.

Изображения и видео скриншотов отдельно не зеркалируются: каталог использует
абсолютные upstream URL. Иконки устанавливаются локально в
`/usr/share/swcatalog/icons/<origin>/<size>/`. XML находится в
`/usr/share/swcatalog/xml/`. Несуществующая кешированная иконка, пустой каталог
или некорректное сопоставление компонент → пакет блокируют выпуск.
Диагностика валидатора и манифест устанавливаются в `/usr/share/doc/<package>/`.

## CI и публикация в lunaos-repo

`.github/workflows/appstream.yml` запускается вручную, ежедневно и при изменении
генератора/политики. Он тестирует настоящий Arch backend, генерирует данные,
собирает пакеты и прикладывает их с отчётами как Actions artifact.

После успешной сборки в `main` workflow обновляет версию корневого PKGBUILD и
`.SRCINFO`, чтобы buildbot `lunaos-repo` заметил новый выпуск. Для этого нужны
права `contents: write` у `GITHUB_TOKEN` и разрешённая запись в `main`.
В одной дате увеличивается `pkgrel`; при смене даты начинается новый `pkgver`.

В `lunaos-repo/packages.tsv` должен быть источник:

```text
lunaos-appstream-data https://github.com/Lenuma-inc/appstream-data.git HEAD -s
```

В самом TSV поля разделяются табуляцией; `before` и `after` остаются пустыми.
Одна запись собирает все три split-пакета. Обновлённый PKGBUILD `libpamac-lunaos`
с зависимостью на `lunaos-appstream-data-common` нужно опубликовать в его
исходном GitLab-репозитории. Тогда buildbot определит порядок сборки по зависимостям
и подпишет/опубликует пакеты обычным механизмом `lunaos-repo`.

После публикации проверка на системе:

```sh
sudo pacman -Syu libpamac-lunaos lunaos-appstream-data-common
appstreamcli status
appstreamcli search cassette
```

Обновление кеша AppStream выполняет штатный pacman hook пакета `appstream`.

## Проверки

```sh
python -m unittest discover -v
```

Проверяются правила отбора, контрольные суммы, резервные зеркала, имена баз,
файлы тестового пакета, безопасная распаковка и сборка pacman-пакета.
Тест настоящего asgen запускается при наличии `appstream-generator`; в CI он обязателен.
