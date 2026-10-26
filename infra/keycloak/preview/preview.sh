#!/usr/bin/env bash
# Renders the ANUM login theme without a Keycloak server (docs/identity.md#login-theme-preview).
#
#   infra/keycloak/preview/preview.sh <work-dir>
#
# Downloads Keycloak 26.4's themes jar, FreeMarker and PatternFly 5 once into <work-dir> (about 8 MB),
# renders every page of Render.java in English and Arabic into <work-dir>/site, and prints the folder.
# Open the .html files in a browser, or run screenshots.js (Playwright) for the five viewports.
# Needs java 17+ (javac not required) and curl, unzip, tar.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
THEME="$HERE/../themes/anum/login"
WORK="${1:?usage: preview.sh <work-dir>}"
mkdir -p "$WORK"
cd "$WORK"
KEYCLOAK=26.4.0
[ -f keycloak-themes.jar ] || curl -fsSL -o keycloak-themes.jar "https://repo1.maven.org/maven2/org/keycloak/keycloak-themes/$KEYCLOAK/keycloak-themes-$KEYCLOAK.jar"
[ -f freemarker.jar ] || curl -fsSL -o freemarker.jar https://repo1.maven.org/maven2/org/freemarker/freemarker/2.3.34/freemarker-2.3.34.jar
if [ ! -d themes/theme ]; then
  mkdir -p themes && unzip -q -o keycloak-themes.jar 'theme/base/login/*' 'theme/keycloak.v2/login/*' -d themes
fi
if [ ! -d pf/package ]; then
  mkdir -p pf && (cd pf && npm pack --silent @patternfly/patternfly@5.4.2 >/dev/null && \
    tar xzf ./*.tgz package/patternfly.min.css package/patternfly-addons.css package/assets/fonts/webfonts package/assets/fonts/RedHatText package/assets/fonts/RedHatDisplay && rm -f ./*.tgz)
fi
rm -rf site && mkdir -p site/res site/common/vendor/patternfly-v5
# Resources the way Keycloak serves them: base, then keycloak.v2, then anum on top.
for theme in "$WORK/themes/theme/base/login/resources" "$WORK/themes/theme/keycloak.v2/login/resources" "$THEME/resources"; do
  [ -d "$theme" ] && cp -r "$theme/." site/res/
done
cp pf/package/patternfly.min.css pf/package/patternfly-addons.css site/common/vendor/patternfly-v5/
mkdir -p site/common/vendor/patternfly-v5/assets && cp -r pf/package/assets/fonts site/common/vendor/patternfly-v5/assets/
java -cp freemarker.jar "$HERE/Render.java" "$WORK/themes/theme" "$THEME" "$WORK/site"
echo "$WORK/site"
