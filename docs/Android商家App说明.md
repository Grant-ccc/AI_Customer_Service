# Android商家App内测版

2026-10-14：新增独立 `mobile` 工程，使用Capacitor承载现有HTTPS商家工作台，适合先验证vivo手机的登录、接手、回复、结束与发货表上传。没有改变已确认的人工规则，也没有实现过期补发、进度查询和后台推送。

## 内测范围

安装后直接打开商家登录页面，使用已有账号。App与手机浏览器各自保留会话凭据；首次需要重新登录。登录凭据不写入安装包。页面与API同源，保留现有Cookie和CSRF保护；禁止明文HTTP和混合内容，不允许扩大导航域名白名单。

当前仅供内测，Capacitor的远程server.url方式不是正式生产发布方案。工作台仍依赖公网入口和电脑服务在线；网页更新重新构建后会被App加载，域名更换则需要修改mobile/capacitor.config.json并重新打包。正式版本应另外设计本地前端、后端来源校验与稳定服务器。

App打开时使用现有工作台轮询，不提供系统通知。切到后台、锁屏、划掉App后都不能承诺持续轮询或提醒。没有接入vivo推送，也没有申请通知权限。

## 编译与安装

GitHub Actions的 `Merchant Android APK` 在mobile工程变更推送后构建内测APK，也可手动运行。成功后下载 `huachuangsan-merchant-debug` 构建产物，解压取得 `app-debug.apk`。这是debug签名内测包，不发布到应用商店。云构建debug签名可能变化，升级安装签名不一致时需卸载旧包并重新登录；正式版需要稳定签名。

本机编译需要Android Studio、Android SDK及Java 21；当前机器未检测到该工具链。准备完成后运行：

```powershell
cd mobile
npm ci
npm run sync
cd android
.\gradlew.bat assembleDebug
```

APK位于 `mobile/android/app/build/outputs/apk/debug/app-debug.apk`。SDK路径、签名文件、APK及本地配置不提交仓库。

## vivo实机验收

1. 安装并首次登录，确认进入商家工作台，不是客户端咨询页。
2. QQ客户转人工，在App内找到待处理事项、查看摘要、接手、回复，检查QQ实际收到消息。
3. 结束处理后再次咨询，检查现有流程正常；尚未开发的结果必填及补发不作为本版能力。
4. 手机文件选择器上传模拟xlsx，预览并确认，不使用个人数据截图。
5. 测试软键盘、长记录滚动、系统返回键、退出登录与重开App。
6. 停止网络或电脑服务，确认不会误认为消息已送达；重连后检查状态。
7. 锁屏和划掉App不作为推送验收：此版本没有推送。

编译成功仅证明生成安装包；上述实机行为与后台通知均不能据此宣称通过。

## 首次构建结果

2026-10-14依赖审计为0个已报告漏洞，Android同步通过。[首次云构建](https://github.com/Grant-ccc/AI_Customer_Service/actions/runs/38519765641)成功完成assembleDebug并上传APK；对应工程提交df724f0。后续dcaa83d仅清理模板空白。vivo实机安装与收发验收尚未完成。
