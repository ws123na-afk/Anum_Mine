<#-- ANUM login layout (docs/identity.md#login-theme). A copy of Keycloak 26.4 keycloak.v2 template.ftl
     with a different page frame: a calm brand panel beside the form card, the ANUM mark,
     the language picker above the card and a small notice on the local development realm.
     The scripts, the username macro and every section the pages fill in are unchanged, so
     all keycloak.v2 pages keep working. On a Keycloak upgrade, diff this file against the
     new keycloak.v2 template.ftl. -->
<#import "field.ftl" as field>
<#import "footer.ftl" as loginFooter>
<#macro username>
  <#assign label>
    <#if !realm.loginWithEmailAllowed>${msg("username")}<#elseif !realm.registrationEmailAsUsername>${msg("usernameOrEmail")}<#else>${msg("email")}</#if>
  </#assign>
  <@field.group name="username" label=label>
    <div class="${properties.kcInputGroup}">
      <div class="${properties.kcInputGroupItemClass} ${properties.kcFill}">
        <span class="${properties.kcInputClass} ${properties.kcFormReadOnlyClass}">
          <input id="kc-attempted-username" value="${auth.attemptedUsername}" readonly>
        </span>
      </div>
      <div class="${properties.kcInputGroupItemClass}">
        <button id="reset-login" class="${properties.kcFormPasswordVisibilityButtonClass} kc-login-tooltip" type="button" 
              aria-label="${msg('restartLoginTooltip')}" onclick="location.href='${url.loginRestartFlowUrl}'">
            <i class="fa-sync-alt fas" aria-hidden="true"></i>
            <span class="kc-tooltip-text">${msg("restartLoginTooltip")}</span>
        </button>
      </div>
    </div>
  </@field.group>
</#macro>

<#-- The ANUM 360 mark (apps/web/src/Shell.tsx AnumMark), filled by anum.css with the accent gradient. -->
<#macro anumMark size=32>
<svg class="anum-mark" width="${size}" height="${size}" viewBox="0 0 48 48" aria-hidden="true" focusable="false"><path d="M3 41 15.5 9h6L34 41h-7.2l-8.3-22.4L10.2 41H3Zm17.6 0L29 18.6l3.6 9.3L28.9 41h-8.3Zm12.6-17.7L38 9h1.9L45 41h-6.9l-4.9-17.7Z"/></svg>
</#macro>

<#macro registrationLayout bodyClass="" displayInfo=false displayMessage=true displayRequiredFields=false>
<!DOCTYPE html>
<html class="${properties.kcHtmlClass!}" lang="${lang}"<#if realm.internationalizationEnabled> dir="${(locale.rtl)?then('rtl','ltr')}"</#if>>

<head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
    <meta name="color-scheme" content="light${darkMode?then(' dark', '')}">
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <#if properties.meta?has_content>
        <#list properties.meta?split(' ') as meta>
            <meta name="${meta?split('==')[0]}" content="${meta?split('==')[1]}"/>
        </#list>
    </#if>
    <title>${msg("loginTitle",(realm.displayName!''))}</title>
    <link rel="icon" type="image/svg+xml" href="${url.resourcesPath}/img/anum-mark.svg" />
    <link rel="preload" href="${url.resourcesPath}/fonts/inter-latin-var.woff2" as="font" type="font/woff2" crossorigin />
    <meta name="theme-color" content="#0f7a69" />
    <#if properties.stylesCommon?has_content>
        <#list properties.stylesCommon?split(' ') as style>
            <link href="${url.resourcesCommonPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <#if properties.styles?has_content>
        <#list properties.styles?split(' ') as style>
            <link href="${url.resourcesPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <script type="importmap">
        {
            "imports": {
                "rfc4648": "${url.resourcesCommonPath}/vendor/rfc4648/rfc4648.js"
            }
        }
    </script>
    <#if darkMode>
      <script type="module" async blocking="render">
          const DARK_MODE_CLASS = "${properties.kcDarkModeClass}";
          const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

          updateDarkMode(mediaQuery.matches);
          mediaQuery.addEventListener("change", (event) => updateDarkMode(event.matches));

          function updateDarkMode(isEnabled) {
            const { classList } = document.documentElement;

            if (isEnabled) {
              classList.add(DARK_MODE_CLASS);
            } else {
              classList.remove(DARK_MODE_CLASS);
            }
          }
      </script>
    </#if>
    <#if properties.scripts?has_content>
        <#list properties.scripts?split(' ') as script>
            <script src="${url.resourcesPath}/${script}" type="text/javascript"></script>
        </#list>
    </#if>
    <#if scripts??>
        <#list scripts as script>
            <script src="${script}" type="text/javascript"></script>
        </#list>
    </#if>
    <script type="module" src="${url.resourcesPath}/js/passwordVisibility.js"></script>
    <script type="module">
        import { startSessionPolling } from "${url.resourcesPath}/js/authChecker.js";

        startSessionPolling(
            "${url.ssoLoginInOtherTabsUrl?no_esc}"
        );
    </script>
    <script type="module">
        document.addEventListener("click", (event) => {
            const link = event.target.closest("a[data-once-link]");

            if (!link) {
                return;
            }

            if (link.getAttribute("aria-disabled") === "true") {
                event.preventDefault();
                return;
            }

            const { disabledClass } = link.dataset;

            if (disabledClass) {
                link.classList.add(...disabledClass.trim().split(/\s+/));
            }

            link.setAttribute("role", "link");
            link.setAttribute("aria-disabled", "true");
        });
    </script>
    <#if authenticationSession??>
        <script type="module">
            import { checkAuthSession } from "${url.resourcesPath}/js/authChecker.js";

            checkAuthSession(
                "${authenticationSession.authSessionIdHash}"
            );
        </script>
    </#if>
    <script>
      // Workaround for https://bugzilla.mozilla.org/show_bug.cgi?id=1404468
      const isFirefox = true;
    </script>
</head>

<body id="keycloak-bg" class="${properties.kcBodyClass!} anum-body" data-page-id="login-${pageId}">
<div class="${properties.kcLogin!} anum-shell">
  <div class="${properties.kcLoginContainer!} anum-stage">
    <div class="anum-column">
      <div class="anum-toolbar">
        <div class="anum-topbrand" role="img" aria-label="${msg('anumBrandName')}">
          <@anumMark 28 /><span class="anum-wordmark" dir="ltr">ANUM</span>
        </div>
        <#if (realm.displayName!'')?lower_case?contains('local development')>
          <span class="anum-env-notice">${msg("anumLocalNotice")}</span>
        </#if>
        <#if realm.internationalizationEnabled  && locale.supported?size gt 1>
        <div class="${properties.kcLoginMainHeaderUtilities!} anum-language">
          <div class="${properties.kcInputClass!}">
            <select
              aria-label="${msg("languages")}"
              id="login-select-toggle"
              onchange="if (this.value) window.location.href=this.value"
            >
              <#list locale.supported?sort_by("label") as l>
                <option
                  value="${l.url}"
                  lang="${l.languageTag}"
                  ${(l.languageTag == locale.currentLanguageTag)?then('selected','')}
                >
                  ${l.label}
                </option>
              </#list>
            </select>
            <span class="${properties.kcFormControlUtilClass}">
              <span class="${properties.kcFormControlToggleIcon!}">
                <svg class="pf-v5-svg" viewBox="0 0 320 512" fill="currentColor" aria-hidden="true" role="img" width="1em" height="1em">
                  <path d="M31.3 192h257.3c17.8 0 26.7 21.5 14.1 34.1L174.1 354.8c-7.8 7.8-20.5 7.8-28.3 0L17.2 226.1C4.6 213.5 13.5 192 31.3 192z"></path>
                </svg>
              </span>
            </span>
          </div>
        </div>
        </#if>
      </div>

      <main class="${properties.kcLoginMain!} anum-card">
        <div class="${properties.kcLoginMainHeader!}">
          <h1 class="${properties.kcLoginMainTitle!}" id="kc-page-title"><#nested "header"></h1>
        </div>
        <div class="${properties.kcLoginMainBody!}">
          <#if !(auth?has_content && auth.showUsername() && !auth.showResetCredentials())>
              <#if displayRequiredFields>
                  <div class="${properties.kcContentWrapperClass!}">
                      <div class="${properties.kcLabelWrapperClass!} subtitle">
                          <span class="${properties.kcInputHelperTextItemTextClass!}">
                            <span class="${properties.kcInputRequiredClass!}">*</span> ${msg("requiredFields")}
                          </span>
                      </div>
                  </div>
              </#if>
          <#else>
              <#if displayRequiredFields>
                  <div class="${properties.kcContentWrapperClass!}">
                      <div class="${properties.kcLabelWrapperClass!} subtitle">
                          <span class="${properties.kcInputHelperTextItemTextClass!}">
                            <span class="${properties.kcInputRequiredClass!}">*</span> ${msg("requiredFields")}
                          </span>
                      </div>
                      <div class="${properties.kcFormClass} ${properties.kcContentWrapperClass}">
                          <#nested "show-username">
                          <@username />
                      </div>
                  </div>
              <#else>
                  <div class="${properties.kcFormClass} ${properties.kcContentWrapperClass}">
                    <#nested "show-username">
                    <@username />
                  </div>
              </#if>
          </#if>

          <#-- App-initiated actions should not see warning messages about the need to complete the action -->
          <#-- during login.                                                                               -->
          <#if displayMessage && message?has_content && (message.type != 'warning' || !isAppInitiatedAction??)>
              <div class="${properties.kcAlertClass!} pf-m-${(message.type = 'error')?then('danger', message.type)}" role="${(message.type = 'error')?then('alert', 'status')}">
                  <div class="${properties.kcAlertIconClass!}">
                      <#if message.type = 'success'><span class="${properties.kcFeedbackSuccessIcon!}"></span></#if>
                      <#if message.type = 'warning'><span class="${properties.kcFeedbackWarningIcon!}"></span></#if>
                      <#if message.type = 'error'><span class="${properties.kcFeedbackErrorIcon!}"></span></#if>
                      <#if message.type = 'info'><span class="${properties.kcFeedbackInfoIcon!}"></span></#if>
                  </div>
                  <span class="${properties.kcAlertTitleClass!} kc-feedback-text">${kcSanitize(message.summary)?no_esc}</span>
              </div>
          </#if>

          <#nested "form">

          <#if auth?has_content && auth.showTryAnotherWayLink()>
            <form id="kc-select-try-another-way-form" action="${url.loginAction}" method="post" novalidate="novalidate">
                <input type="hidden" name="tryAnotherWay" value="on"/>
                <a id="try-another-way" href="javascript:document.forms['kc-select-try-another-way-form'].requestSubmit()"
                    class="${properties.kcButtonSecondaryClass} ${properties.kcButtonBlockClass} ${properties.kcMarginTopClass}">
                      ${kcSanitize(msg("doTryAnotherWay"))?no_esc}
                </a>
            </form>
          </#if>

          <div class="${properties.kcLoginMainFooter!}">
              <#nested "socialProviders">

              <#if displayInfo>
                  <div id="kc-info" class="${properties.kcLoginMainFooterBand!} ${properties.kcFormClass}">
                      <div id="kc-info-wrapper" class="${properties.kcLoginMainFooterBandItem!}">
                          <#nested "info">
                      </div>
                  </div>
              </#if>
          </div>
        </div>

        <div class="${properties.kcLoginMainFooter!}">
            <@loginFooter.content/>
        </div>
      </main>
      <p class="anum-help">${msg("anumHelp")}</p>
    </div>

    <#-- Brand panel: decoration plus two short statements the product already makes elsewhere. After the form in the
         reading order, before it on screen (grid areas in anum.css). -->
    <aside class="anum-brand" aria-labelledby="anum-brand-title">
      <div class="anum-brand-inner">
        <div class="anum-brand-logo" role="img" aria-label="${msg('anumBrandName')}">
          <@anumMark 40 /><span class="anum-wordmark" dir="ltr">ANUM</span>
        </div>
        <div class="anum-brand-copy">
          <p class="anum-brand-title" id="anum-brand-title">${msg("anumBrandHeadline")}</p>
          <p class="anum-brand-text">${msg("anumBrandText")}</p>
          <ul class="anum-brand-points">
            <li>${msg("anumBrandPointTwoStep")}</li>
            <li>${msg("anumBrandPointSeparate")}</li>
          </ul>
        </div>
      </div>
      <svg class="anum-motif" viewBox="0 0 640 640" aria-hidden="true" focusable="false" preserveAspectRatio="xMidYMid slice">
        <defs>
          <pattern id="anum-grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="currentColor" stroke-width="1"/></pattern>
          <radialGradient id="anum-fade" cx="50%" cy="62%" r="62%"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
          <mask id="anum-mask"><rect width="640" height="640" fill="url(#anum-fade)"/></mask>
        </defs>
        <g mask="url(#anum-mask)"><rect width="640" height="640" fill="url(#anum-grid)"/></g>
        <path class="anum-motif-peaks" d="M120 580 300 180h60L540 580h-72L330 256 192 580Z" fill="none" stroke="currentColor" stroke-width="2"/>
      </svg>
    </aside>
  </div>
</div>
</body>
</html>
</#macro>
