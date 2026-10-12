<#-- Replaces keycloak.v2's password-validation.ftl (used by register.ftl and
     login-update-password.ftl). Instead of listing what is wrong after the field loses
     focus, the page shows every rule of the realm's password policy under the field and
     marks each one as met while the person types. Keycloak still checks the policy on
     submit; this is guidance only. -->
<#macro templates>
</#macro>

<#macro script field="">
    <#if passwordPolicies??>
    <script type="module">
        import { attachPasswordRules } from "${url.resourcesPath}/js/password-rules.js";

        const rules = [
            <#if (passwordPolicies.length!0) gt 0>{ name: "length", value: ${passwordPolicies.length?c}, label: "${msg('anumPasswordRuleLength', passwordPolicies.length?c)?js_string}" },</#if>
            <#if (passwordPolicies.maxLength!0) gt 0>{ name: "maxLength", value: ${passwordPolicies.maxLength?c}, label: "${msg('anumPasswordRuleMaxLength', passwordPolicies.maxLength?c)?js_string}" },</#if>
            <#if (passwordPolicies.lowerCase!0) gt 0>{ name: "lowerCase", value: ${passwordPolicies.lowerCase?c}, label: "${msg('anumPasswordRuleLowerCase', passwordPolicies.lowerCase?c)?js_string}" },</#if>
            <#if (passwordPolicies.upperCase!0) gt 0>{ name: "upperCase", value: ${passwordPolicies.upperCase?c}, label: "${msg('anumPasswordRuleUpperCase', passwordPolicies.upperCase?c)?js_string}" },</#if>
            <#if (passwordPolicies.digits!0) gt 0>{ name: "digits", value: ${passwordPolicies.digits?c}, label: "${msg('anumPasswordRuleDigits', passwordPolicies.digits?c)?js_string}" },</#if>
            <#if (passwordPolicies.specialChars!0) gt 0>{ name: "specialChars", value: ${passwordPolicies.specialChars?c}, label: "${msg('anumPasswordRuleSpecialChars', passwordPolicies.specialChars?c)?js_string}" },</#if>
            <#-- With email as the username the email rule says it all. -->
            <#if passwordPolicies.notUsername && !(realm.registrationEmailAsUsername && passwordPolicies.notEmail)>{ name: "notUsername", value: 1, label: "${msg('anumPasswordRuleNotUsername')?js_string}" },</#if>
            <#if passwordPolicies.notEmail>{ name: "notEmail", value: 1, label: "${msg('anumPasswordRuleNotEmail')?js_string}" },</#if>
        ];

        attachPasswordRules(document, "${field}", rules, {
            heading: "${msg('anumPasswordRulesHeading')?js_string}",
            met: "${msg('anumPasswordRuleMet')?js_string}",
            unmet: "${msg('anumPasswordRuleUnmet')?js_string}",
        });
    </script>
    </#if>
</#macro>
