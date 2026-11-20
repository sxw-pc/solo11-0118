# 公交遗失物交接台

车辆收班后离线登记拾获物、封袋交接和认领证据，决定保管责任与是否交还。三个独立入口为 `found-import`、`custody-import`、`claim-import`；各自保存原始证据、业务实体、状态和跨链决定。所有数据为离线合成 fixture，不访问网络。

安装：`python -m pip install . --no-deps --no-build-isolation`。演示：`python -m lostproperty demo`。测试：`python -m pytest -q`。各入口接受 `--db` 和 `--file`；`report --db` 输出逐实体状态与来源。

拾获物绑定车次、座位、发现时间和离线乘车凭证。封袋交接按序号、保管人和封签核对；断签会阻断认领。两个不同见证人核实完整袋内实物后，`bag-reconcile` 可重封并恢复此前被保管链阻断的认领。竞合失主由 `claim-adjudicate` 引用乘车凭证、座位及物品特征选择归还者，落败申请保留；`return_event` 记录实际交还。命令 `demo-decisions` 展示断签、扣留和复核后的交还状态变化。

## 同车跨趟拾获来源核查

同一车辆同一座位连续跑两趟时，仅凭拾获人填写的车次与两小时时间窗不足以确定来源；两趟都可能落在窗内。`clearing-register` 为线路启用一种清空证明程序（车载画面主证 `cabin_camera_continuous`，或双人实地清扫主证 `dual_attested_cleaning`），并在拾获材料入库时固定，之后登记的程序不追溯适用。`clear-observe-import` 记录某车某座的空置观察（设备/见证人、时段、原始摘要、分段画面）或物品进入记录；分段之间有空缺、观察结果为占用、车辆或座位不符的观察都不会切断任何车次。只有被固定程序认可、且完整无缺的清空证据或物品进入记录才能排除更早车次；证据互相矛盾或缺失时保留全部候选。

`found-import` 与后续材料会重新计算来源核查，结果按修订号存入 `source_review`；`report --db` 的 `source_reviews` 逐件列出可能来源车次、应寻访乘客（按每趟 `boarding_record` 真实座位占用，不同车次同座号不会合并）、被排除车次的证据，以及仍未排除的原因。`contact-import` 留存已联系历史；晚到材料只生成新修订与 `contact_correction` 更正说明，不重写旧名单。命令 `demo-source-review` 用 R1(10:00)/R2(11:00) 同一座位 S 展示：车载画面主证下只联系 P2，双人清扫主证下联系 P1 与 P2。两种寻访都不判定失主。
