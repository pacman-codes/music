import 'dart:async';
import 'package:flutter/material.dart';
import 'api.dart';
import 'player.dart';

String positionLabel(int ms){
  final seconds=ms~/1000;
  return '${seconds~/60}:${(seconds%60).toString().padLeft(2,'0')}';
}

class DeviceSyncPage extends StatefulWidget {
  final LainApi api;
  final LainPlayer player;
  const DeviceSyncPage({super.key,required this.api,required this.player});
  @override State<DeviceSyncPage> createState()=>_DeviceSyncPageState();
}

class _DeviceSyncPageState extends State<DeviceSyncPage>{
  List<dynamic> devices=[];
  bool busy=false;
  String? status;
  @override void initState(){super.initState();unawaited(refresh());}
  Future<void> run(Future<void> Function() task)async{
    if(busy)return;
    setState((){busy=true;status=null;});
    try{await task();}
    catch(e){if(mounted)setState(()=>status=e is ApiError?e.toString():'Не удалось синхронизировать устройство');}
    finally{if(mounted)setState(()=>busy=false);}
  }
  Future<void> fetch()async{
    final result=await widget.api.request('GET','/v1/devices') as Map;
    if(mounted)setState(()=>devices=result['results'] as List);
  }
  Future<void> refresh()=>run(fetch);
  Future<void> publish()=>run(()async{
    final ok=await widget.player.publishCheckpoint();
    if(mounted)setState(()=>status=ok?'Позиция сохранена на сервере':widget.player.syncError??'Нет активной онлайн-очереди или уже идёт синхронизация.');
    await fetch();
  });
  Future<void> clear()async{
    final confirmed=await showDialog<bool>(context:context,builder:(ctx)=>AlertDialog(
      title:const Text('Удалить сохранённую позицию?'),
      content:const Text('Будет удалена только контрольная точка этого устройства. Локальная очередь останется. Автоматическую публикацию отключим.'),
      actions:[TextButton(onPressed:()=>Navigator.pop(ctx,false),child:const Text('Отмена')),
        FilledButton(onPressed:()=>Navigator.pop(ctx,true),child:const Text('Удалить'))]));
    if(confirmed!=true||!mounted)return;
    await run(()async{
      await widget.player.clearCheckpoint();
      if(mounted)setState(()=>status='Контрольная точка удалена');
      await fetch();
    });
  }
  Future<void> restore(Map row)=>run(()async{
    final data=Map<String,dynamic>.from(await widget.api.request('GET','/v1/devices/${row['id']}/checkpoint') as Map);
    if(!mounted)return;
    final tracks=widget.api.rows(data['results']);
    final index=data['index'] as int;
    final track=tracks[index];
    final omitted=data['omitted'] as int;
    final confirmed=await showDialog<bool>(context:context,builder:(ctx)=>AlertDialog(
      title:Text('Продолжить с ${row['label']}?'),
      content:Text('${track.artist} — ${track.title}\nПозиция: ${positionLabel(data['position_ms'] as int)}\nТреков: ${tracks.length}${omitted>0?'\nНедоступных пропущено: $omitted':''}\n\nТекущая очередь этого телефона будет заменена. Воспроизведение останется на паузе, другой телефон не остановится.'),
      actions:[TextButton(onPressed:()=>Navigator.pop(ctx,false),child:const Text('Отмена')),
        FilledButton(onPressed:()=>Navigator.pop(ctx,true),child:const Text('Восстановить'))]));
    if(confirmed!=true||!mounted)return;
    final ok=await widget.player.restoreCheckpoint(data);
    if(mounted)setState(()=>status=ok?'Очередь восстановлена. Нажми Play.':'Не удалось восстановить очередь');
  });
  Widget deviceRow(Map row){
    final checkpoint=row['checkpoint'] as Map?;
    final self=row['current']==true;
    final updated=checkpoint==null?null:DateTime.fromMillisecondsSinceEpoch((checkpoint['updated_at'] as int)*1000,isUtc:true).toLocal();
    final time=updated==null?'':'${updated.day.toString().padLeft(2,'0')}.${updated.month.toString().padLeft(2,'0')} ${updated.hour.toString().padLeft(2,'0')}:${updated.minute.toString().padLeft(2,'0')}';
    return Card(child:Padding(padding:const EdgeInsets.all(14),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
      Text('${row['label']}${self?' · это устройство':''}',style:Theme.of(context).textTheme.titleMedium),
      if(checkpoint==null)const Text('Нет сохранённой очереди')else...[
        const SizedBox(height:8),
        Text('${checkpoint['artist']??''} — ${checkpoint['title']??'Запись удалена'}'),
        Text('${positionLabel(checkpoint['position_ms'] as int)} · ${checkpoint['count']} треков'),
        Text('Сохранено: $time'),
        OutlinedButton(onPressed:busy?null:()=>restore(row),child:const Text('Восстановить очередь')),
      ],
    ])));
  }
  @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Устройства'),actions:[
    IconButton(onPressed:busy?null:refresh,icon:const Icon(Icons.refresh)),
  ]),body:ValueListenableBuilder<int>(valueListenable:widget.player.changes,builder:(context,_,child)=>ListView(
    padding:const EdgeInsets.all(16),children:[
      SwitchListTile(contentPadding:EdgeInsets.zero,title:const Text('Автоматически сохранять позицию'),
        subtitle:const Text('Примерно каждые 15 секунд онлайн-воспроизведения и при паузе. Выключено по умолчанию.'),
        value:widget.player.shareProgress,onChanged:busy?null:(value)=>run(()=>widget.player.setShareProgress(value))),
      const Text('Каждое устройство хранит свою очередь. Офлайн-очереди остаются только на телефоне.'),
      FilledButton.icon(onPressed:busy||widget.player.current==null||widget.player.offlineOnly?null:publish,icon:const Icon(Icons.cloud_upload),label:const Text('Сохранить текущую позицию')),
      OutlinedButton(onPressed:busy?null:clear,child:const Text('Удалить позицию этого устройства')),
      if(busy)const LinearProgressIndicator(),
      if(status!=null)Text(status!),
      for(final row in devices)deviceRow(row as Map),
      if(devices.isEmpty&&!busy)const Text('Нет подключённых устройств или список ещё не загружен.'),
    ])));
}
