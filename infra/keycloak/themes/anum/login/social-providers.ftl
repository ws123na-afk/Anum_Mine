<#-- ANUM replacement for keycloak.v2's social-providers.ftl: one full-width "Continue with ..." button
     per identity provider, the same size for each, with an icon for Google, Apple and Microsoft
     (any other provider gets its name and a generic mark). Ids, data attributes and links are
     unchanged from keycloak.v2. -->
<#macro show social>
  <div class="${properties.kcLoginMainFooterBand!} anum-social-divider">
      <span class="${properties.kcLoginMainFooterBandItem!} ${properties.kcLoginMainFooterHelperText!}">
          ${msg("anumSocialDivider")}
      </span>
  </div>
  <div id="kc-social-providers" class="${properties.kcFormSocialAccountSectionClass!}">
      <ul class="${properties.kcFormSocialAccountListClass!} anum-social-list">
          <#list social.providers as p>
              <li class="${properties.kcFormSocialAccountListItemClass!}">
                  <a data-once-link data-disabled-class="${properties.kcFormSocialAccountListButtonDisabledClass!}" id="social-${p.alias}"
                          class="${properties.kcFormSocialAccountListButtonClass!} anum-social-button" aria-label="${msg('anumContinueWith', p.displayName)}"
                          href="${p.loginUrl}">
                      <span class="anum-social-icon" aria-hidden="true">
                        <#switch p.alias>
                            <#case "google">
                                <svg viewBox="0 0 48 48" class="google">
                                    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"></path>
                                    <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"></path>
                                    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"></path>
                                    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"></path>
                                </svg>
                                <#break>
                            <#case "apple">
                                <svg viewBox="0 0 384 512" fill="currentColor">
                                    <path d="M318.7 268.7c-.2-36.7 16.4-64.4 50-84.8-18.8-26.9-47.2-41.7-84.7-44.6-35.5-2.8-74.3 20.7-88.5 20.7-15 0-49.4-19.7-76.4-19.7C63.3 141.2 4 184.8 4 273.5q0 39.3 14.4 81.2c12.8 36.7 59 126.7 107.2 125.2 25.2-.6 43-17.9 75.8-17.9 31.8 0 48.3 17.9 76.4 17.9 48.6-.7 90.4-82.5 102.6-119.3-65.2-30.7-61.7-90-61.7-91.9zm-56.6-164.2c27.3-32.4 24.8-61.9 24-72.5-24.1 1.4-52 16.4-67.9 34.9-17.5 19.8-27.8 44.3-25.6 71.9 26.1 2 49.9-11.4 69.5-34.3z"/>
                                </svg>
                                <#break>
                            <#case "microsoft">
                                <svg viewBox="0 0 23 23">
                                    <path fill="#f25022" d="M1 1h10v10H1z"/><path fill="#7fba00" d="M12 1h10v10H12z"/><path fill="#00a4ef" d="M1 12h10v10H1z"/><path fill="#ffb900" d="M12 12h10v10H12z"/>
                                </svg>
                                <#break>
                            <#default>
                                <svg viewBox="0 0 512 512" fill="currentColor">
                                    <path d="M239.1 6.3l-208 78c-18.7 7-31.1 25-31.1 45v225.1c0 18.2 10.3 34.8 26.5 42.9l208 104c13.5 6.8 29.4 6.8 42.9 0l208-104c16.3-8.1 26.5-24.8 26.5-42.9V129.3c0-20-12.4-37.9-31.1-44.9l-208-78C262 2.2 250 2.2 239.1 6.3zM256 68.4l192 72v1.1l-192 78-192-78v-1.1l192-72zm32 356V275.5l160-65v133.9l-160 80z"/>
                                </svg>
                        </#switch>
                      </span>
                      <span class="anum-social-name">${msg("anumContinueWith", p.displayName!)}</span>
                  </a>
              </li>
          </#list>
      </ul>
  </div>
</#macro>
