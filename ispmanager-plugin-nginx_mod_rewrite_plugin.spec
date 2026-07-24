Name: ispmanager-plugin-nginx_mod_rewrite_plugin
Version: 0.0.1
Release: 1%{?dist}
Summary: Модуль nginx mod_rewrite
License: Apache License 2.0
URL: https://github.com/bayrepo/ngx_http_apache_rewrite_module
Source0: %{name}-%{version}.tar.gz
BuildRequires: wget
Requires: openssl-devel
Requires: pcre-devel
Requires: zlib-devel
Requires: gcc
Requires: gcc-c++
Requires: make
Requires: wget
Requires: python3

%description
Модуль добавления поддержки mod_rewrite правил и .htaccess в nginx

%prep
%setup -q

%build
# nothing to build

%install
mkdir -p %{buildroot}/usr/local/mgr5/etc/plugins/ispmgr
mkdir -p %{buildroot}/usr/local/mgr5/addon
cp -r usr/* %{buildroot}/usr
mkdir -p %{buildroot}/usr/local/ispmanager-mod_rewrite/utils
wget -O %{buildroot}/usr/local/ispmanager-mod_rewrite/mod_rewrite.zip https://github.com/bayrepo/ngx_http_apache_rewrite_module/archive/refs/heads/main.zip
cp utils/build_mod_rewrite.py %{buildroot}/usr/local/ispmanager-mod_rewrite/utils
mkdir -p %{buildroot}/usr/local/ispmanager-mod_rewrite/settings
touch %{buildroot}/usr/local/ispmanager-mod_rewrite/.lock
mkdir -p %{buildroot}/usr/local/ispmanager-mod_rewrite/patches
cp payload/* %{buildroot}/usr/local/ispmanager-mod_rewrite/patches/

%triggerin -- nginx
if [ -e /usr/local/ispmanager-mod_rewrite/settings/installed.cfg ]; then
    /usr/local/ispmanager-mod_rewrite/utils/build_mod_rewrite.py --nodeps
fi

%posttrans
killall core

%postun
if [ "$1" == "0" ]; then
    killall core
    if [ -e /usr/local/ispmanager-mod_rewrite/settings ]; then
        rm -f /usr/local/ispmanager-mod_rewrite/settings/*
    fi
fi

%preun
if [ "$1" == "0" ]; then
    rm -f /usr/share/nginx/modules/ngx_http_apache_rewrite_module.conf
    rm -f /usr/lib64/nginx/modules/ngx_http_apache_rewrite_module.so
    rm -f /usr/local/ispmanager-mod_rewrite/settings/installed.cfg
    systemctl restart nginx
fi

%files
/usr/local/mgr5/etc/xml/ispmgr_mod_nginx_mod_rewrite_plugin.xml
/usr/local/mgr5/etc/plugins/ispmgr/nginx_mod_rewrite_plugin.xml
%attr(700, root, root) /usr/local/mgr5/addon/nginx_mod_rewrite_plugin.py
%attr(700, root, root) /usr/local/mgr5/addon/nginx_form_rewrite_plugin.py
%attr(600, root, root) /usr/local/ispmanager-mod_rewrite/mod_rewrite.zip
%attr(700, root, root) /usr/local/ispmanager-mod_rewrite/utils/build_mod_rewrite.py
%attr(700, root, root) %dir /usr/local/ispmanager-mod_rewrite/settings
%attr(644, root, root) /usr/local/mgr5/skins/icons/mod_rewrite_menu.svg
%attr(644, root, root) /usr/local/mgr5/skins/icons/mod_rewrite_tool.svg
%attr(644, root, root) /usr/local/mgr5/skins/common/plugin-logo/ispmanager-plugin-nginx_mod_rewrite_plugin.png
%attr(600, root, root) /usr/local/ispmanager-mod_rewrite/patches/nginx-vhosts.template.patch
%attr(600, root, root) /usr/local/ispmanager-mod_rewrite/patches/nginx-vhosts-ssl.template.patch
%attr(600, root, root) /usr/local/ispmanager-mod_rewrite/.lock

%changelog
* Mon Jun 29 2026 Alexey BayRepo <a@bayrepo.ru> - 0.0.1-1
- Initial release
