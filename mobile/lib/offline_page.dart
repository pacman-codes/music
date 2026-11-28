import 'dart:async';
import 'package:flutter/material.dart';
import 'api.dart';
import 'cache.dart';
import 'player.dart';

String storageSize(int bytes)=>bytes>=gib?'${(bytes/gib).toStringAsFixed(2)} ГБ':'${(bytes/mib).toStringAsFixed(1)} МБ';

class OfflinePage extends StatefulWidget{
  final LainPlayer player;
  const OfflinePage({super.key,required this.player});
  @override State<OfflinePage> createState()=>_OfflinePageState();
}
class _OfflinePageState extends State<OfflinePage>{
  Map<String,int> usage={};
  bool busy=false;
  String? status;
  AudioCache? get cache=>widget.player.cache;
  @override void initState(){super.initState();unawaited(refresh());}
  Future<void> refresh()async{
    try{final data=await cache?.usage();if(mounted&&data!=null)setState(()=>usage=data);}
    catch(_){if(mounted)setState(()=>status='Не удалось прочитать хранилище');}
  }
  Future<void> remove(Track track)async{
    if(busy)return;
    final confirm=await showDialog<bool>(context:context,builder:(ctx)=>AlertDialog(
      title:const Text('Удалить скачанный файл?'),content:Text(track.title),
      actions:[TextButton(onPressed:()=>Navigator.pop(ctx,false),child:const Text('Отмена')),
        FilledButton(onPressed:()=>Navigator.pop(ctx,true),child:const Text('Удалить'))]));
    if(confirm!=true||!mounted)return;
    setState(()=>busy=true);
    try{
      final ok=await cache?.unpin(track.id);
      if(mounted)setState(()=>status=ok==true?'Удалено с устройства. Медиатека на сервере сохранена.':'Сначала переключи текущую песню.');
    }catch(_){if(mounted)setState(()=>status='Не удалось удалить файл');}
    finally{if(mounted)setState(()=>busy=false);await refresh();}
  }
  @override Widget build(BuildContext context){
    final tracks=cache?.downloads??[];
    return Scaffold(appBar:AppBar(title:const Text('Скачанная музыка'),actions:[
      IconButton(onPressed:busy?null:refresh,icon:const Icon(Icons.refresh)),
    ]),body:ListView(padding:const EdgeInsets.all(16),children:[
      Text('${tracks.length} треков · ${storageSize(usage['offline']??0)}'),
      const Text('Воспроизведение из этого раздела не требует подключения к серверу.'),
      FilledButton.icon(onPressed:tracks.isEmpty||busy?null:()=>widget.player.startOffline(tracks),
        icon:const Icon(Icons.play_arrow),label:const Text('Слушать офлайн')),
      OutlinedButton.icon(onPressed:tracks.isEmpty||busy?null:()=>widget.player.startOffline(List<Track>.of(tracks)..shuffle()),
        icon:const Icon(Icons.shuffle),label:const Text('Перемешать')),
      if(busy)const LinearProgressIndicator(),
      if(status!=null)Text(status!),
      if(tracks.isEmpty)const ListTile(title:Text('Нет скачанных песен'),subtitle:Text('Выбери «Скачать» в меню готового трека.')),
      for(var i=0;i<tracks.length;i++)
        ListTile(leading:const Icon(Icons.download_done),title:Text(tracks[i].title),
          subtitle:Text(tracks[i].artist),onTap:()=>widget.player.startOffline(tracks,initial:i),
          trailing:IconButton(onPressed:busy?null:()=>remove(tracks[i]),icon:const Icon(Icons.delete_outline))),
    ]));
  }
}

class StoragePage extends StatefulWidget{
  final LainPlayer player;
  const StoragePage({super.key,required this.player});
  @override State<StoragePage> createState()=>_StoragePageState();
}
class _StoragePageState extends State<StoragePage>{
  late int cacheMiB,offlineGiB,warm;
  late String quality;
  Map<String,int> usage={};
  bool busy=false;
  String? status;
  AudioCache get cache=>widget.player.cache!;
  @override void initState(){
    super.initState();cacheMiB=cache.cacheMiB;offlineGiB=cache.offlineGiB;warm=cache.warmCount;quality=cache.quality;
    unawaited(refresh());
  }
  Future<void> refresh()async{
    try{final data=await cache.usage();if(mounted)setState(()=>usage=data);}catch(_){}
  }
  Future<void> action(Future<void> Function() work,String success)async{
    if(busy)return;
    setState(()=>busy=true);
    try{await work();if(mounted)setState(()=>status=success);}
    catch(_){if(mounted)setState(()=>status='Не удалось изменить хранилище');}
    finally{if(mounted)setState(()=>busy=false);await refresh();}
  }
  Widget option(String name,int value,List<int> choices,String Function(int) label,ValueChanged<int> changed)=>
    ListTile(contentPadding:EdgeInsets.zero,title:Text(name),trailing:DropdownButton<int>(
      value:value,items:[for(final n in choices)DropdownMenuItem(value:n,child:Text(label(n)))],
      onChanged:busy?null:(n)=>setState(()=>changed(n!))));
  @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Хранилище')),body:ListView(
    padding:const EdgeInsets.all(16),children:[
      Text('Временный кеш: ${storageSize(usage['cache']??0)}'),
      Text('Скачанные треки: ${storageSize(usage['offline']??0)}'),
      option('Лимит кеша',cacheMiB,AudioCache.cacheOptions,(n)=>n>=1024?'${n~/1024} ГБ':'$n МБ',(n)=>cacheMiB=n),
      option('Лимит загрузок',offlineGiB,AudioCache.offlineOptions,(n)=>'$n ГБ',(n)=>offlineGiB=n),
      option('Предзагрузка на телефон',warm,AudioCache.warmOptions,(n)=>n==0?'Выкл.':'$n треков',(n)=>warm=n),
      ListTile(title:const Text('Качество стриминга'),trailing:DropdownButton<String>(value:quality,items:const [DropdownMenuItem(value:'original',child:Text('Оригинал')),DropdownMenuItem(value:'economy',child:Text('Экономия трафика'))],onChanged:busy?null:(v)=>setState(()=>quality=v!))),
      FilledButton(onPressed:busy?null:()=>action(()=>cache.settings(cache:cacheMiB,offlineLimit:offlineGiB,warm:warm,streamQuality:quality),'Настройки сохранены'),child:const Text('Сохранить')),
      OutlinedButton(onPressed:busy?null:()=>action(cache.clearTemporary,'Временный кеш очищен, текущий трек защищён'),child:const Text('Очистить временный кеш')),
      const Divider(),
      const ListTile(title:Text('Экономия трафика'),subtitle:Text('AAC около 96 кбит/с, если файл меньше оригинала. Нужна предварительная обработка на сервере. Уже скачанные оригиналы и постоянные загрузки не перекодируются. Новое качество действует со следующего запуска трека.')),
      const Text('Скачанные файлы не удаляются при очистке кеша. Снижение лимита загрузок не удаляет уже скачанную музыку. Автоматические загрузки выполняются, пока приложение работает.'),
      if(busy)const LinearProgressIndicator(),if(status!=null)Text(status!),
    ]));
}

/// Explicit sequential downloads. The user can stop between tracks; playback
/// continues independently. No claim that a killed app completes downloads.
class DownloadPage extends StatefulWidget{
  final LainPlayer player;
  final List<Track> tracks;
  const DownloadPage({super.key,required this.player,required this.tracks});
  @override State<DownloadPage> createState()=>_DownloadPageState();
}
class _DownloadPageState extends State<DownloadPage>{
  int done=0,failed=0,processed=0;
  bool running=false,cancel=false,started=false;
  String? current;
  Future<void> download()async{
    if(running)return;
    setState((){running=true;started=true;});
    final cache=widget.player.cache;
    if(cache==null){setState(()=>running=false);return;}
    for(final t in widget.tracks){
      if(cancel||!mounted)break;
      setState(()=>current=t.title);
      bool success=false;
      try{success=await cache.pin(t)!=null;}catch(_){}
      if(!mounted)break;
      setState((){processed++;if(success){done++;}else{failed++;}});
    }
    if(mounted)setState((){running=false;current=null;});
  }
  @override void dispose(){cancel=true;super.dispose();}
  @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Скачать музыку')),
    body:ListView(padding:const EdgeInsets.all(24),children:[
      Text('${widget.tracks.length} готовых треков',style:Theme.of(context).textTheme.headlineSmall),
      const SizedBox(height:12),const Text('Загрузка оригинального аудио в постоянное хранилище телефона.'),
      const SizedBox(height:20),
      LinearProgressIndicator(value:widget.tracks.isEmpty?0:processed/widget.tracks.length),
      Text('Готово: $done · ошибок: $failed · обработано: $processed'),
      if(current!=null)Text(current!),
      if(!started)FilledButton(onPressed:widget.tracks.isEmpty?null:download,child:const Text('Начать загрузку')),
      if(running)OutlinedButton(onPressed:cancel?null:()=>setState(()=>cancel=true),child:const Text('Остановить после текущего')),
      if(started&&!running)const Text('Загрузка завершена или остановлена. Готовые файлы доступны офлайн.'),
    ]));
}
