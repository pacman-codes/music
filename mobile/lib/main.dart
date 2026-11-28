import 'dart:async';
import 'package:audio_service/audio_service.dart';
import 'package:audio_session/audio_session.dart';
import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import 'api.dart';
import 'cache.dart';
import 'player.dart';
import 'catalog.dart';
import 'offline_page.dart';
import 'now_playing.dart';
import 'audio_insight.dart';
import 'equalizer.dart';
import 'package:flutter/foundation.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  var enableEq=false;
  if(!kIsWeb&&defaultTargetPlatform==TargetPlatform.android){
    try{enableEq=await Sessions.storage.read(key:androidEqBootKey)=='true';}catch(_){}
  }
  final player=LainPlayer(enableAndroidEq:enableEq);
  await AudioService.init(builder:()=>player,config:const AudioServiceConfig(
    androidNotificationChannelId:'ink.soodoo.lain.playback',
    androidNotificationChannelName:'LAIN · Музыка',
    androidNotificationOngoing:true,
    androidStopForegroundOnPause:true,
  ));
  final audio=await AudioSession.instance;
  await audio.configure(const AudioSessionConfiguration.music());
  runApp(LainApp(player:player));
}

class LainApp extends StatefulWidget {
  final LainPlayer player;
  const LainApp({super.key,required this.player});
  @override State<LainApp> createState()=>_LainAppState();
}

class _LainAppState extends State<LainApp> {
  LainApi? api;
  bool loading=true;
  String? error;
  final server=TextEditingController(text:'https://music.soodoo.ink');
  final code=TextEditingController();
  final label=TextEditingController(text:'Мой телефон');
  @override void initState(){super.initState();unawaited(restore());}
  Future<void> connect(LainApi client) async{
    final cache=await AudioCache.create(client);
    await widget.player.attach(client,cache);
    if(!mounted)return;
    setState(()=>api=client);
  }
  Future<void> restore() async{
    try{final saved=await Sessions.restore();if(saved!=null)await connect(saved);}
    catch(_){if(mounted)setState(()=>error='Не удалось восстановить сессию. Подключись заново.');}
    finally{if(mounted)setState(()=>loading=false);}
  }
  Future<void> pair() async{
    setState((){loading=true;error=null;});
    try{
      final client=await LainApi.pair(server.text,code.text,label.text);
      await Sessions.save(client);
      await connect(client);
      code.clear();
    }catch(e){if(mounted)setState(()=>error=e is FormatException||e is ApiError?e.toString():'Не удалось подключиться');}
    finally{if(mounted)setState(()=>loading=false);}
  }
  Future<void> logout() async{
    final client=api;
    setState(()=>loading=true);
    try{
      await widget.player.disconnect();
      try{await client?.request('DELETE','/v1/session');}catch(_){}
      await Sessions.clear();
    }finally{
      client?.client.close();
      if(mounted)setState((){api=null;loading=false;});
    }
  }
  @override void dispose(){server.dispose();code.dispose();label.dispose();super.dispose();}
  @override Widget build(BuildContext context)=>MaterialApp(
    title:'LAIN',debugShowCheckedModeBanner:false,
    theme:ThemeData(useMaterial3:true,brightness:Brightness.dark,
      colorScheme:ColorScheme.fromSeed(seedColor:const Color(0xffaa98f6),brightness:Brightness.dark),
      scaffoldBackgroundColor:const Color(0xff111016)),
    home:loading?const Scaffold(body:Center(child:CircularProgressIndicator())):
      api!=null?Home(api:api!,player:widget.player,onLogout:logout):
      Scaffold(body:SafeArea(child:Center(child:SingleChildScrollView(
        padding:const EdgeInsets.all(28),
        child:Column(crossAxisAlignment:CrossAxisAlignment.stretch,children:[
          const Text('L A I N',style:TextStyle(fontSize:40,letterSpacing:8)),
          const SizedBox(height:12),const Text('Твоя музыка. Твой сервер.'),
          const SizedBox(height:28),
          TextField(controller:server,keyboardType:TextInputType.url,autocorrect:false,
            decoration:const InputDecoration(labelText:'HTTPS-адрес сервера')),
          TextField(controller:label,decoration:const InputDecoration(labelText:'Название устройства')),
          TextField(controller:code,obscureText:true,autocorrect:false,
            decoration:const InputDecoration(labelText:'Одноразовый ключ привязки')),
          const SizedBox(height:20),
          if(error!=null)Text(error!,style:TextStyle(color:Theme.of(context).colorScheme.error)),
          FilledButton(onPressed:pair,child:const Text('Подключить')),
          const SizedBox(height:8),const Text('Ключ создаётся на сервере и действует 10 минут.'),
        ]),
      )))),
  );
}

class Home extends StatefulWidget{
  final LainApi api;
  final LainPlayer player;
  final Future<void> Function() onLogout;
  const Home({super.key,required this.api,required this.player,required this.onLogout});
  @override State<Home> createState()=>_HomeState();
}

class _HomeState extends State<Home>{
  int tab=0;
  final search=TextEditingController();
  List<Track> library=[];
  List<dynamic> playlists=[],history=[];
  int? before;
  int? playlistId;
  String? playlistName;
  List<Track> playlistTracks=[];
  int playlistPage=0,playlistPages=1;
  bool loading=false;
  String? error;
  String mode='balanced';
  Map<String,dynamic>? settings,taste;
  List<dynamic> smart=[];
  int _request=0;
  LainApi get api=>widget.api;
  LainPlayer get player=>widget.player;
  @override void initState(){super.initState();unawaited(loadLibrary());}
  @override void dispose(){search.dispose();super.dispose();}
  Future<void> run(Future<void> Function() fn) async{
    if(loading)return;
    final id=++_request;
    setState((){loading=true;error=null;});
    try{await fn();}catch(e){if(mounted&&id==_request)setState(()=>error=e is ApiError?e.toString():'Не удалось загрузить данные');}
    finally{if(mounted&&id==_request)setState(()=>loading=false);}
  }
  Future<void> loadLibrary({bool more=false})=>run(()async{
    final data=await api.library(query:search.text.trim(),before:more?before:null);
    if(!mounted)return;
    setState((){library=more?[...library,...api.rows(data['results'])]:api.rows(data['results']);before=data['next_before'] as int?;});
  });
  Future<void> loadPlaylists()=>run(()async{
    final rows=await api.playlists();
    final auto=await api.request('GET','/v1/smart-playlists') as Map;
    if(mounted)setState((){playlists=rows;smart=auto['results'] as List;});
  });
  Future<void> openPlaylist(int id,String name,int page)=>run(()async{
    final data=await api.playlist(id,page);
    if(!mounted)return;
    setState((){playlistId=id;playlistName=name;playlistTracks=api.rows(data['results']);
      playlistPage=data['page'] as int;playlistPages=data['pages'] as int;});
  });
  Future<void> loadProfile()=>run(()async{
    final values=await api.settings();
    final rows=await api.history();
    final profile=await api.request('GET','/v1/taste/profile') as Map;
    if(mounted)setState((){settings=values;history=rows;taste=Map<String,dynamic>.from(profile);});
  });
  void chooseTab(int index){
    setState(()=>tab=index);
    if(index==1&&library.isEmpty)unawaited(loadLibrary());
    if(index==2)unawaited(loadPlaylists());
    if(index==4)unawaited(loadProfile());
  }
  void message(String text){if(mounted)ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:Text(text)));}
  Future<void> feedback(Track t) async{
    final action=await showModalBottomSheet<String>(context:context,builder:(ctx)=>SafeArea(child:ListView(
      shrinkWrap:true,padding:const EdgeInsets.all(16),children:[
        Text(t.title,style:Theme.of(ctx).textTheme.titleLarge),
        Text(t.artist),const SizedBox(height:8),
        for(final e in const {'add':'Добавлю','maybe':'Может добавлю','never':'Никогда','later':'Не сейчас','interesting':'Интересно, но не слушать'}.entries)
          ListTile(title:Text(e.value),onTap:()=>Navigator.pop(ctx,e.key)),
        ListTile(leading:const Icon(Icons.playlist_add),title:const Text('В плейлист'),onTap:()=>Navigator.pop(ctx,'playlist')),
        if(t.playable)ListTile(leading:const Icon(Icons.graphic_eq),title:const Text('Анализ и похожие по звучанию'),onTap:()=>Navigator.pop(ctx,'insight')),
        if(t.playable)ListTile(leading:const Icon(Icons.download),title:const Text('Скачать офлайн'),onTap:()=>Navigator.pop(ctx,'download')),
        if(t.playable)ListTile(leading:const Icon(Icons.queue_music),title:const Text('Следующим'),onTap:()=>Navigator.pop(ctx,'next')),
        if(tab==2&&playlistId!=null)ListTile(leading:const Icon(Icons.remove_circle_outline),title:const Text('Убрать из плейлиста'),onTap:()=>Navigator.pop(ctx,'remove')),
        if(tab==2&&playlistId!=null&&isCustomPlaylist)...[
          ListTile(title:const Text('Выше'),onTap:()=>Navigator.pop(ctx,'up')),
          ListTile(title:const Text('Ниже'),onTap:()=>Navigator.pop(ctx,'down')),
        ],
        const Divider(),const Text('Оценка 1–10'),
        Wrap(children:[for(var n=1;n<=10;n++)TextButton(onPressed:()=>Navigator.pop(ctx,'rate:$n'),child:Text('$n'))]),
      ],
    )));
    if(action==null||!mounted)return;
    try{
      if(action=='insight'){
        await Navigator.push<void>(context,MaterialPageRoute(builder:(_)=>AudioInsightPage(api:api,player:player,track:t)));return;
      }
      if(action=='download'){await downloadTracks([t]);return;}
      if(action=='playlist'){await addToPlaylist(t);return;}
      if(action=='next'){await player.enqueueNext(t);return;}
      if(action=='remove'){await api.request('DELETE','/v1/playlists/$playlistId/tracks/${t.id}');}
      else if(action=='up'||action=='down'){await api.request('PUT','/v1/playlists/$playlistId/tracks/${t.id}/position',body:{'delta':action=='up'?-1:1});}
      else if(action.startsWith('rate:')){await api.rate(t.id,int.parse(action.substring(5)));}
      else{await api.react(t.id,action);}
      message('Сохранено');
      if(tab==1){await loadLibrary();}else if(tab==2&&playlistId!=null){await openPlaylist(playlistId!,playlistName!,playlistPage);}
    }catch(e){message(e.toString());}
  }
  Future<void> prepare(Track t)async{
    try{
      final states=await api.prefetch([t.id]);
      if(states.isEmpty)return;
      final state=states.first['state'];
      message(switch(state){'ready'=>'Аудио готово. Обнови список.','queued'||'running'=>'Сервер готовит аудио.','failed'=>'Источник не удалось загрузить.','busy'=>'Очередь загрузки заполнена.',_=>'Нет одобренного источника.'});
    }catch(e){message(e.toString());}
  }
  Widget trackRow(Track t,List<Track> rows){
    final ready=rows.where((x)=>x.playable).toList();
    return ListTile(
      leading:Icon(t.playable?Icons.music_note_rounded:Icons.cloud_off_outlined),
      title:Text(t.title,maxLines:1,overflow:TextOverflow.ellipsis),
      subtitle:Text('${t.artist}${t.rating!=null?' · ${t.rating}/10':''}${t.playable?'':' · нет аудио'}',maxLines:1,overflow:TextOverflow.ellipsis),
      onTap:t.playable?()=>player.start(ready,initial:ready.indexWhere((x)=>x.id==t.id)):()=>prepare(t),
      trailing:IconButton(icon:Icon(t.liked?Icons.favorite:Icons.more_horiz),onPressed:()=>feedback(t)),
    );
  }
  Future<void> downloadTracks(List<Track> rows)async{
    final ready=rows.where((t)=>t.playable).toList();
    if(ready.isEmpty){message('Нет готового аудио');return;}
    if(!mounted)return;
    await Navigator.push<void>(context,MaterialPageRoute(builder:(_)=>DownloadPage(player:player,tracks:ready)));
  }
  Future<void> confirmLogout()async{
    final confirmed=await showDialog<bool>(context:context,builder:(ctx)=>AlertDialog(
      title:const Text('Отключить устройство?'),
      content:const Text('Локальные скачанные файлы, очередь и ещё не синхронизированная история будут удалены с этого устройства. Музыка и коллекция на сервере останутся.'),
      actions:[TextButton(onPressed:()=>Navigator.pop(ctx,false),child:const Text('Отмена')),
        FilledButton(onPressed:()=>Navigator.pop(ctx,true),child:const Text('Отключить'))]));
    if(confirmed==true&&mounted)await widget.onLogout();
  }
  Future<void> showOffline()async{
    await Navigator.push<void>(context,MaterialPageRoute(builder:(_)=>OfflinePage(player:player)));
  }
  Future<void> showStorage()async{
    if(player.cache==null)return;
    await Navigator.push<void>(context,MaterialPageRoute(builder:(_)=>StoragePage(player:player)));
  }
  Future<void> showNowPlaying()async{
    if(player.current==null)return;
    await Navigator.push<void>(context,MaterialPageRoute(builder:(_)=>NowPlayingPage(player:player,feedback:feedback,download:downloadTracks)));
  }
  Future<void> downloadPlaylist()async{
    final id=playlistId;
    if(id==null)return;
    try{
      final data=await api.request('GET','/v1/playlists/$id/queue') as Map;
      if(mounted)await downloadTracks(api.rows(data['results']));
    }catch(e){message(e.toString());}
  }
  Widget home()=>ListView(padding:const EdgeInsets.all(16),children:[
    Card(child:Padding(padding:const EdgeInsets.all(22),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
      const Text('ПЕРСОНАЛЬНОЕ РАДИО',style:TextStyle(letterSpacing:2,fontSize:11)),
      const SizedBox(height:14),Text('Моя волна',style:Theme.of(context).textTheme.headlineLarge),
      const SizedBox(height:6),const Text('Знакомые треки, новые находки и серверная предзагрузка.'),
      const SizedBox(height:16),
      DropdownButton<String>(value:mode,isExpanded:true,items:const [
        DropdownMenuItem(value:'balanced',child:Text('Сбалансированная')),
        DropdownMenuItem(value:'discover',child:Text('Открытия')),
        DropdownMenuItem(value:'favorites',child:Text('Избранное')),
      ],onChanged:(v)=>setState(()=>mode=v!)),
      const SizedBox(height:8),
      FilledButton.icon(onPressed:player.busy?null:()=>player.startWave(mode),icon:const Icon(Icons.play_arrow),label:const Text('Слушать')),
    ]))),
    ListTile(leading:const Icon(Icons.download_done),title:const Text('Скачанная музыка'),subtitle:Text('${player.cache?.downloads.length??0} треков'),onTap:showOffline),
    if(player.preparing.isNotEmpty)ListTile(leading:const Icon(Icons.cloud_download),title:Text('Сервер готовит: ${player.preparing.length}')),
    Text('Очередь',style:Theme.of(context).textTheme.titleLarge),
    if(player.tracks.isEmpty)const ListTile(title:Text('Пока пусто')),
    for(var i=0;i<player.tracks.length;i++)
      ListTile(selected:i==player.index,leading:Icon(i==player.index?Icons.equalizer:Icons.music_note),title:Text(player.tracks[i].title),
        subtitle:Text(player.tracks[i].artist),onTap:()=>player.skipToQueueItem(i),trailing:IconButton(icon:const Icon(Icons.more_horiz),onPressed:()=>feedback(player.tracks[i]))),
  ]);
  Widget collection()=>Column(children:[
    Padding(padding:const EdgeInsets.all(12),child:TextField(controller:search,onSubmitted:(_)=>loadLibrary(),
      decoration:InputDecoration(prefixIcon:const Icon(Icons.search),hintText:'Песня или исполнитель',suffixIcon:IconButton(icon:const Icon(Icons.arrow_forward),onPressed:()=>loadLibrary())))),
    Expanded(child:RefreshIndicator(onRefresh:loadLibrary,child:ListView(children:[
      for(final t in library)trackRow(t,library),
      if(library.isEmpty)const ListTile(title:Text('Нет треков')),
      if(before!=null)TextButton(onPressed:loading?null:()=>loadLibrary(more:true),child:const Text('Ещё')),
    ]))),
  ]);
  bool get isCustomPlaylist=>playlists.any((p)=>p['id']==playlistId&&p['kind']=='custom');
  Future<String?> askName(String title,{String initial=''})async{
    final field=TextEditingController(text:initial);
    try{return await showDialog<String>(context:context,builder:(ctx)=>AlertDialog(
      title:Text(title),content:TextField(controller:field,maxLength:80,autofocus:true),
      actions:[TextButton(onPressed:()=>Navigator.pop(ctx),child:const Text('Отмена')),
        FilledButton(onPressed:()=>Navigator.pop(ctx,field.text.trim()),child:const Text('Сохранить'))]));
    }finally{field.dispose();}
  }
  Future<void> createPlaylist()async{
    final name=await askName('Новый плейлист');
    if(name==null||name.isEmpty||!mounted)return;
    try{await api.request('POST','/v1/playlists',body:{'name':name});await loadPlaylists();}
    catch(e){message(e.toString());}
  }
  Future<void> renamePlaylist()async{
    final id=playlistId;
    if(id==null)return;
    final name=await askName('Переименовать',initial:playlistName??'');
    if(name==null||name.isEmpty||!mounted)return;
    try{await api.request('PUT','/v1/playlists/$id',body:{'name':name});
      await loadPlaylists();await openPlaylist(id,name,playlistPage);
    }catch(e){message(e.toString());}
  }
  Future<void> deletePlaylist()async{
    final id=playlistId;
    if(id==null)return;
    final confirmed=await showDialog<bool>(context:context,builder:(ctx)=>AlertDialog(
      title:const Text('Удалить плейлист?'),content:Text(playlistName??''),
      actions:[TextButton(onPressed:()=>Navigator.pop(ctx,false),child:const Text('Отмена')),
        FilledButton(onPressed:()=>Navigator.pop(ctx,true),child:const Text('Удалить'))]));
    if(confirmed!=true||!mounted)return;
    try{await api.request('DELETE','/v1/playlists/$id');if(mounted)setState(()=>playlistId=null);await loadPlaylists();}
    catch(e){message(e.toString());}
  }
  Future<void> addToPlaylist(Track t)async{
    try{
      final rows=await api.playlists();
      if(!mounted)return;
      final id=await showModalBottomSheet<int>(context:context,builder:(ctx)=>SafeArea(child:ListView(
        shrinkWrap:true,children:[
          for(final p in rows)ListTile(title:Text(p['name'] as String),onTap:()=>Navigator.pop(ctx,p['id'] as int)),
        ])));
      if(id==null)return;
      await api.request('POST','/v1/playlists/$id/tracks',body:{'track_id':t.id});
      message('Добавлено в плейлист');
      if(tab==2&&playlistId!=null)await openPlaylist(playlistId!,playlistName!,playlistPage);
    }catch(e){message(e.toString());}
  }
  Future<void> playFullPlaylist({bool shuffle=false,int? from})async{
    final id=playlistId;
    if(id==null)return;
    try{
      final data=await api.request('GET','/v1/playlists/$id/queue') as Map;
      final rows=api.rows(data['results']).where((t)=>t.playable).toList();
      if(rows.isEmpty){message('Нет готового аудио');return;}
      if(shuffle)rows.shuffle();
      final initial=from==null?0:rows.indexWhere((t)=>t.id==from);
      await player.start(rows,initial:initial<0?0:initial);
    }catch(e){message(e.toString());}
  }
  Future<void> openSmart(Map row)async{
    try{
      final data=await api.request('GET','/v1/smart-playlists/${row['key']}') as Map;
      final rows=api.rows(data['results']);
      if(!mounted)return;
      await Navigator.push<void>(context,MaterialPageRoute(builder:(_)=>Scaffold(
        appBar:AppBar(title:Text(data['title'] as String)),
        body:ListView(children:[
          Padding(padding:const EdgeInsets.all(12),child:Text(data['description'] as String)),
          if(data['truncated']==true)const Text('Первые 1000 записей'),
          FilledButton(onPressed:rows.any((t)=>t.playable)?()=>player.start(rows):null,child:const Text('Слушать доступные')),
          for(final t in rows)trackRow(t,rows),
        ]),
      )));
    }catch(e){message(e.toString());}
  }
  Widget playlistsPage()=>ListView(children:[
    if(playlistId==null)...[
      ListTile(leading:const Icon(Icons.download_done),title:const Text('Скачанная музыка'),onTap:showOffline),
      ListTile(leading:const Icon(Icons.add),title:const Text('Создать плейлист'),onTap:createPlaylist),
      for(final row in playlists)ListTile(leading:const Icon(Icons.queue_music),title:Text(row['name'] as String),
        subtitle:Text('${row['count']} треков'),onTap:()=>openPlaylist(row['id'] as int,row['name'] as String,0)),
      if(playlists.isEmpty)const ListTile(title:Text('Нет плейлистов')),
      const Divider(),const Padding(padding:EdgeInsets.all(12),child:Text('Умные плейлисты')),
      for(final row in smart)ListTile(leading:const Icon(Icons.auto_awesome),title:Text(row['title'] as String),subtitle:Text('${row['available_count']} готовых / ${row['count']}'),onTap:()=>openSmart(Map<String,dynamic>.from(row as Map))),
    ]else...[
      ListTile(leading:IconButton(icon:const Icon(Icons.arrow_back),onPressed:()=>setState(()=>playlistId=null)),title:Text(playlistName??'')),
      Wrap(alignment:WrapAlignment.center,spacing:8,children:[
        FilledButton.icon(onPressed:()=>playFullPlaylist(),icon:const Icon(Icons.play_arrow),label:const Text('Весь плейлист')),
        OutlinedButton.icon(onPressed:()=>playFullPlaylist(shuffle:true),icon:const Icon(Icons.shuffle),label:const Text('Перемешать')),
        OutlinedButton.icon(onPressed:downloadPlaylist,icon:const Icon(Icons.download),label:const Text('Скачать доступные')),
        if(isCustomPlaylist)...[TextButton(onPressed:renamePlaylist,child:const Text('Переименовать')),TextButton(onPressed:deletePlaylist,child:const Text('Удалить'))],
      ]),
      for(final t in playlistTracks)ListTile(leading:Icon(t.playable?Icons.music_note:Icons.cloud_off),title:Text(t.title),subtitle:Text(t.artist),onTap:t.playable?()=>playFullPlaylist(from:t.id):()=>prepare(t),trailing:IconButton(icon:const Icon(Icons.more_horiz),onPressed:()=>feedback(t))),
      Row(mainAxisAlignment:MainAxisAlignment.center,children:[
        IconButton(onPressed:playlistPage>0?()=>openPlaylist(playlistId!,playlistName!,playlistPage-1):null,icon:const Icon(Icons.chevron_left)),
        Text('${playlistPage+1} / $playlistPages'),
        IconButton(onPressed:playlistPage+1<playlistPages?()=>openPlaylist(playlistId!,playlistName!,playlistPage+1):null,icon:const Icon(Icons.chevron_right)),
      ]),
    ],
  ]);
  Widget profile()=>ListView(padding:const EdgeInsets.all(16),children:[
    if(taste!=null)...[
      Text('Музыкальный профиль',style:Theme.of(context).textTheme.titleLarge),
      Text('${taste!['available_count']} готовых · ${taste!['liked_count']} понравились · ${taste!['rated_count']} оценено'),
      for(final a in taste!['artists'] as List)Padding(padding:const EdgeInsets.symmetric(vertical:6),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
        Text(a['name'] as String),LinearProgressIndicator(value:((a['affinity'] as num)/4.5).clamp(0,1).toDouble()),
      ])),
      const Text('Вес предпочтения — внутренний коэффициент, не процент совпадения.'),
      const SizedBox(height:20),
    ],
    Text('Настройки Волны',style:Theme.of(context).textTheme.titleLarge),
    if(settings!=null)...[
      DropdownButton<String>(value:settings!['preset'] as String,isExpanded:true,items:const [
        DropdownMenuItem(value:'balanced',child:Text('Баланс')),
        DropdownMenuItem(value:'familiar',child:Text('Знакомая музыка')),
        DropdownMenuItem(value:'explore',child:Text('Исследование')),
      ],onChanged:(v)=>setState(()=>settings!['preset']=v)),
      Text('Разнообразие: ${settings!['diversity']}%'),
      Slider(value:(settings!['diversity'] as int).toDouble(),max:100,divisions:100,onChanged:(v)=>setState(()=>settings!['diversity']=v.round())),
      Text('Окно повторов: ${settings!['repeat_window']} треков'),
      Slider(value:(settings!['repeat_window'] as int).toDouble(),max:100,divisions:100,onChanged:(v)=>setState(()=>settings!['repeat_window']=v.round())),
      FilledButton(onPressed:loading?null:()=>run(()async{await api.saveSettings(settings!);message('Настройки сохранены');}),child:const Text('Сохранить')),
    ],
    const SizedBox(height:22),Text('История воспроизведения',style:Theme.of(context).textTheme.titleLarge),
    for(final row in history)ListTile(title:Text(row['title'] as String),
      subtitle:Text('${row['artist']} · ${(row['listened_ms'] as int)~/1000} сек.')),
    if(history.isEmpty)const Text('Пока нет подтверждённых событий.'),
    const SizedBox(height:20),ListTile(title:const Text('Сервер'),subtitle:Text(api.origin.toString())),
    if(!kIsWeb&&defaultTargetPlatform==TargetPlatform.android)
      ListTile(leading:const Icon(Icons.equalizer),title:const Text('Эквалайзер Android'),onTap:()=>Navigator.push<void>(context,MaterialPageRoute(builder:(_)=>EqualizerPage(player:player)))),
    ListTile(leading:const Icon(Icons.storage),title:const Text('Хранилище и предзагрузка'),subtitle:Text('${player.cache?.cacheMiB??1024} МБ кеша'),onTap:showStorage),
    ListTile(leading:const Icon(Icons.download_done),title:const Text('Скачанная музыка'),onTap:showOffline),
    ListTile(title:const Text('Синхронизация истории'),subtitle:Text('Ожидает отправки: ${player.journal?.pendingCount??0}'),trailing:IconButton(icon:const Icon(Icons.sync),onPressed:()async{await player.journal?.flush();player.changed();})),
    OutlinedButton(onPressed:confirmLogout,child:const Text('Отключить устройство')),
  ]);
  Widget playerBar(){
    final t=player.current;
    if(t==null)return const SizedBox.shrink();
    return Material(color:const Color(0xff29243a),child:Padding(padding:const EdgeInsets.all(8),child:Column(mainAxisSize:MainAxisSize.min,children:[
      Row(children:[
        Expanded(child:ListTile(dense:true,title:Text(t.title,maxLines:1,overflow:TextOverflow.ellipsis),subtitle:Text(t.artist),onTap:showNowPlaying)),
        IconButton(onPressed:player.busy?null:player.skipToPrevious,icon:const Icon(Icons.skip_previous)),
        IconButton(onPressed:player.busy?null:player.engine.playing?player.pause:player.play,icon:Icon(player.engine.playing?Icons.pause_circle_filled:Icons.play_circle_fill,size:34)),
        IconButton(onPressed:player.busy?null:player.skipToNext,icon:const Icon(Icons.skip_next)),
      ]),
      StreamBuilder<Duration>(stream:player.engine.positionStream,builder:(context,snapshot){
        final duration=player.engine.duration?.inMilliseconds??0;
        final pos=player.displayedPosition.clamp(0,duration).toDouble();
        return Row(children:[
          Text(formatTime(duration==0?player.displayedPosition:pos.toInt()),style:const TextStyle(fontSize:11)),
          Expanded(child:Slider(value:pos,max:duration>0?duration.toDouble():1,onChanged:duration>0&&!player.busy?(v){}:null,onChangeEnd:duration>0&&!player.busy?(v)=>player.seek(Duration(milliseconds:v.round())):null)),
          Text(formatTime(duration),style:const TextStyle(fontSize:11)),
        ]);
      }),
      if(player.offlineOnly)const Text('Офлайн',style:TextStyle(fontSize:11)),
      if(player.engine.processingState==ProcessingState.loading||player.engine.processingState==ProcessingState.buffering)const LinearProgressIndicator(),
    ])));
  }
  String formatTime(int ms){final seconds=ms~/1000;return '${seconds~/60}:${(seconds%60).toString().padLeft(2,'0')}';}
  @override Widget build(BuildContext context)=>ValueListenableBuilder<int>(valueListenable:player.changes,builder:(context,_,child)=>Scaffold(
    appBar:AppBar(title:const Text('L A I N'),actions:[IconButton(onPressed:loading||tab==3?null:()=>switch(tab){0=>player.startWave(mode),1=>loadLibrary(),2=>playlistId==null?loadPlaylists():openPlaylist(playlistId!,playlistName!,playlistPage),3=>Future<void>.value(),_=>loadProfile()},icon:const Icon(Icons.refresh))]),
    body:SafeArea(child:Column(children:[
      if(loading)const LinearProgressIndicator(),
      if(error!=null||player.error!=null)Padding(padding:const EdgeInsets.all(8),child:Text(error??player.error!,style:TextStyle(color:Theme.of(context).colorScheme.error))),
      Expanded(child:switch(tab){0=>home(),1=>collection(),2=>playlistsPage(),3=>CatalogPage(api:api,player:player),_=>profile()}),
      playerBar(),
    ])),
    bottomNavigationBar:NavigationBar(selectedIndex:tab,onDestinationSelected:chooseTab,destinations:const [
      NavigationDestination(icon:Icon(Icons.waves),label:'Волна'),
      NavigationDestination(icon:Icon(Icons.search),label:'Музыка'),
      NavigationDestination(icon:Icon(Icons.library_music),label:'Плейлисты'),
      NavigationDestination(icon:Icon(Icons.explore),label:'Открытия'),
      NavigationDestination(icon:Icon(Icons.person_outline),label:'Профиль'),
    ]),
  ));
}
